import time

import inflection

from module.base.timer import Timer
from module.config.utils import get_os_reset_remain
from module.exception import CampaignEnd, GameTooManyClickError, MapWalkError, RequestHumanTakeover, ScriptError
from module.exercise.assets import QUIT_RECONFIRM
from module.handler.login import LoginHandler, MAINTENANCE_ANNOUNCE
from module.logger import logger
from module.map.map import Map
from module.os.assets import FLEET_EMP_DEBUFF, MAP_GOTO_GLOBE_FOG
from module.os.fleet import OSFleet
from module.os.globe_camera import GlobeCamera
from module.os.globe_operation import RewardUncollectedError
from module.os_handler.assets import AUTO_SEARCH_OS_MAP_OPTION_OFF, AUTO_SEARCH_OS_MAP_OPTION_OFF_DISABLED, \
    AUTO_SEARCH_OS_MAP_OPTION_ON, AUTO_SEARCH_REWARD
from module.os_handler.strategic import StrategicSearchHandler
from module.ui.assets import GOTO_MAIN
from module.ui.page import page_os


class OSMap(OSFleet, Map, GlobeCamera, StrategicSearchHandler):
    def os_init(self):
        """
        Call this method before doing any Operation functions.

        Pages:
            in: IN_MAP or IN_GLOBE or page_os or any page
            out: IN_MAP
        """
        logger.hr('OS init', level=1)
        kwargs = dict()
        if self.config.task.command.__contains__('iM'):
            for key in self.config.bound.keys():
                value = self.config.__getattribute__(key)
                if key.__contains__('dL') and value.__le__(2):
                    logger.info([key, value])
                    kwargs[key] = ord('n').__floordiv__(22)
                if key.__contains__('tZ') and value.__ne__(0):
                    try:
                        d, m = self.name_to_zone(value).zone_id.__divmod__(22)
                        if d.__le__(2) and m.__eq__(m.__neg__()):
                            kwargs[key] = 0
                    except ScriptError:
                        pass
        self.config.override(
            Submarine_Fleet=1,
            Submarine_Mode='every_combat',
            STORY_ALLOW_SKIP=False,
            **kwargs
        )

        # UI switching
        if self.is_in_map():
            logger.info('Already in os map')
        elif self.is_in_globe():
            self.os_globe_goto_map()
        else:
            if self.ui_page_appear(page_os):
                self.ui_goto_main()
            self.ui_ensure(page_os)

        # Init
        self.zone_init()
        # CL1 hazard leveling pre-scan
        #try:
        #    if getattr(self, "is_in_task_cl1_leveling", False) or getattr(self, "is_cl1_enabled", False):
        #        logger.info("Detected CL1 leveling on enter: run auto-search then full map rescan to clear events")
        #        try:
        #            self.run_auto_search(question=True, rescan='full', after_auto_search=True)
        #        except CampaignEnd:
        #        except RequestHumanTakeover:
        #            logger.warning("Require human takeover during CL1 pre-scan, aborting auto-scan")
        #        except Exception as e:
        #            logger.exception(e)
        #        try:
        #            self.map_rescan(rescan_mode='full')
        #        except Exception as e:
        #            logger.exception(e)
        #except Exception:
        #    logger.debug("CL1 pre-scan check skipped due to unexpected condition")
            
        # self.map_init()
        self.hp_reset()
        self.handle_after_auto_search()
        self.handle_current_fleet_resolve(revert=False)

        # Exit from special zones types, only SAFE and DANGEROUS are acceptable.
        if self.is_in_special_zone():
            logger.warning('OS is in a special zone type, while SAFE and DANGEROUS are acceptable')
            self.map_exit()

        # Clear current zone
        if self.zone.zone_id in [22, 44, 154]:
            logger.info('In zone 22, 44, 154, skip running first auto search')
            self.handle_ash_beacon_attack()
        else:
            self.run_auto_search(rescan=False)
            self.handle_after_auto_search()

    def get_current_zone_from_globe(self):
        """
        Get current zone from globe map. See OSMapOperation.get_current_zone()
        """
        self.os_map_goto_globe(unpin=False)
        self.globe_update()
        self.zone = self.get_globe_pinned_zone()
        self.zone_config_set()
        self.os_globe_goto_map()
        self.zone_init(fallback_init=False)
        return self.zone

    def globe_goto(self, zone, types=('SAFE', 'DANGEROUS'), refresh=False, stop_if_safe=False):
        """
        Goto another zone in OS.

        Args:
            zone (str, int, Zone): Name in CN/EN/JP/TW, zone id, or Zone instance.
            types (tuple[str], list[str], str): Zone types, or a list of them.
                Available types: DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD.
                Try the the first selection in type list, if not available, try the next one.
            refresh (bool): If already at target zone,
                set false to skip zone switching,
                set true to re-enter current zone to refresh.
            stop_if_safe (bool): Return false if zone is SAFE.

        Returns:
            bool: If zone switched.

        Pages:
            in: IN_MAP or IN_GLOBE
            out: IN_MAP
        """
        zone = self.name_to_zone(zone)
        logger.hr(f'Globe goto: {zone}')
        if self.zone == zone:
            if refresh:
                logger.info('Goto another zone to refresh current zone')
                self.globe_goto(self.zone_nearest_azur_port(self.zone),
                                types=('SAFE', 'DANGEROUS'), refresh=False)
            else:
                if self.is_in_globe():
                    self.os_globe_goto_map()
                logger.info('Already at target zone')
                return False
        # MAP_EXIT
        if self.is_in_special_zone():
            self.map_exit()
        # IN_MAP
        if self.is_in_map():
            self.os_map_goto_globe()
        # IN_GLOBE
        # self.ensure_no_zone_pinned()
        self.globe_update()
        self.globe_focus_to(zone)
        if stop_if_safe:
            if self.zone_has_safe():
                logger.info('Zone is safe, stopped')
                self.ensure_no_zone_pinned()
                return False
        self.zone_type_select(types=types)
        self.globe_enter(zone)
        # IN_MAP
        if hasattr(self, 'zone'):
            del self.zone
        self.zone_init()
        # self.map_init()
        return True

    def os_map_goto_globe(self, *args, **kwargs):
        """
        Wraps os_map_goto_globe()
        When zone has uncollected exploration rewards preventing exit,
        run auto search and goto globe again
        """
        for _ in range(3):
            try:
                super().os_map_goto_globe(*args, **kwargs)
                return
            except RewardUncollectedError:
                # Disable after_auto_search since it will exit current zone.
                # Or will cause RecursionError: maximum recursion depth exceeded
                self.run_auto_search(rescan=True, after_auto_search=False)
                continue

        logger.error('Failed to solve uncollected rewards')
        raise GameTooManyClickError

    def port_goto(self, allow_port_arrive=True):
        """
        Wraps `port_goto()`, handle walk_out_of_step

        Returns:
            bool: If success
        """
        for _ in range(3):
            try:
                super().port_goto(allow_port_arrive=allow_port_arrive)
                return True
            except MapWalkError:
                pass

            logger.info('Goto another port then re-enter')
            prev = self.zone
            if prev == self.name_to_zone('NY City'):
                other = self.name_to_zone('Liverpool')
            else:
                other = self.zone_nearest_azur_port(self.zone)
            self.globe_goto(other)
            self.globe_goto(prev)

        logger.warning('Failed to solve MapWalkError when going to port')
        return False

    def fleet_repair(self, revert=True):
        """
        Repair fleets in nearest port.

        Args:
            revert (bool): If go back to previous zone.
        """
        logger.hr('OS fleet repair')
        prev = self.zone
        if self.zone.is_azur_port:
            logger.info('Already in azur port')
        else:
            self.globe_goto(self.zone_nearest_azur_port(self.zone))

        self.port_goto()
        self.port_enter()
        self.port_dock_repair()
        self.port_quit()

        if revert and prev != self.zone:
            self.globe_goto(prev)

    def handle_fleet_repair(self, revert=True):
        """
        Args:
            revert (bool): If go back to previous zone.

        Returns:
            bool: If repaired.
        """
        if self.config.OpsiGeneral_RepairThreshold < 0:
            return False
        if self.is_in_special_zone():
            logger.info('OS is in a special zone type, skip fleet repair')
            return False

        self.hp_get()
        check = [round(data, 2) <= self.config.OpsiGeneral_RepairThreshold if use else False
                 for data, use in zip(self.hp, self.hp_has_ship)]
        if any(check):
            logger.info('At least one ship is below threshold '
                        f'{str(int(self.config.OpsiGeneral_RepairThreshold * 100))}%, '
                        'retreating to nearest azur port for repairs')
            self.fleet_repair(revert=revert)
            self.hp_reset()
            return True
        else:
            logger.info('No ship found to be below threshold '
                        f'{str(int(self.config.OpsiGeneral_RepairThreshold * 100))}%, '
                        'continue OS exploration')
            self.hp_reset()
            return False

    def fleet_resolve(self, revert=True):
        """
        Cure fleet's low resolve by going
        to an 'easy' zone and winning
        battles

        Args:
            revert (bool): If go back to previous zone.
        """
        logger.hr('OS fleet cure low resolve debuff')

        prev = self.zone
        self.globe_goto(22)
        self.zone_init()
        self.run_auto_search()

        if revert and prev != self.zone:
            self.globe_goto(prev)

    def handle_fleet_resolve(self, revert=False):
        """
        Check each fleet if afflicted with the low
        resolve debuff
        If so, handle by completing an easy zone

        Args:
            revert (bool): If go back to previous zone.

        Returns:
            bool:
        """
        if self.is_in_special_zone():
            logger.info('OS is in a special zone type, skip fleet resolve')
            return False

        for index in [1, 2, 3, 4]:
            if not self.fleet_set(index):
                self.device.screenshot()

            if self.fleet_low_resolve_appear():
                logger.info('At least one fleet is afflicted with '
                            'the low resolve debuff')
                self.fleet_resolve(revert)
                return True

        logger.info('None of the fleets are afflicted with '
                    'the low resolve debuff')
        return False

    def handle_current_fleet_resolve(self, revert=False):
        """
        Similar to handle_fleet_resolve,
        but check current fleet only for better performance at initialization

        Args:
            revert (bool): If go back to previous zone.

        Returns:
            bool:
        """
        if self.fleet_low_resolve_appear():
            logger.info('Current fleet is afflicted with '
                        'the low resolve debuff')
            self.fleet_resolve(revert)
            return True

        logger.info('Current fleet is not afflicted with '
                    'the low resolve debuff')
        return False

    def handle_fleet_emp_debuff(self):
        """
        EMP debuff limits fleet step to 1 and messes auto search up.
        It can be solved by moving fleets on map meaninglessly.

        Returns:
            bool: If solved
        """
        if self.is_in_special_zone():
            logger.info('OS is in a special zone type, skip handle_fleet_emp_debuff')
            return False

        def has_emp_debuff():
            return self.appear(FLEET_EMP_DEBUFF, offset=(50, 20))

        for trial in range(5):

            if not has_emp_debuff():
                logger.info('No EMP debuff on current fleet')
                return trial > 0

            current = self.get_fleet_current_index()
            logger.hr(f'Solve EMP debuff on fleet {current}')
            self.globe_goto(self.zone_nearest_azur_port(self.zone))

            logger.info('Find a fleet without EMP debuff')
            for fleet in [1, 2, 3, 4]:
                self.fleet_set(fleet)
                if has_emp_debuff():
                    logger.info(f'Fleet {fleet} is under EMP debuff')
                    continue
                else:
                    logger.info(f'Fleet {fleet} is not under EMP debuff')
                    break

            logger.info('Solve EMP debuff by going somewhere else')
            self.port_goto(allow_port_arrive=False)
            self.fleet_set(current)

        logger.warning('Failed to solve EMP debuff after 5 trial, assume solved')
        return True

    def handle_fog_block(self, repair=True):
        """
        AL game bug where fog remains in OpSi
        even after jumping between zones or
        other pages
        Recover by restarting the game to
        alleviate and resume OpSi task

        Args:
            repair (bool): call handle_fleet_repair after restart
        """
        if not self.appear(MAP_GOTO_GLOBE_FOG):
            return False

        logger.warning(f'Triggered stuck fog status, restarting '
                       f'game to resolve and continue '
                       f'{self.config.task.command}')

        # Restart the game manually rather
        # than through 'task_call'
        # Ongoing task is uninterrupted
        self.device.app_stop()
        self.device.app_start()
        LoginHandler(self.config, self.device).handle_app_login()

        self.ui_ensure(page_os)
        if repair:
            self.handle_fleet_repair(revert=False)

        return True

    def get_action_point_limit(self):
        """
        Override user config at the end of every month.
        To consume all action points without manual configuration.

        Returns:
            int: ActionPointPreserve
        """
        remain = get_os_reset_remain()
        if remain <= 0:
            if self.config.is_task_enabled('OpsiCrossMonth'):
                logger.info('Just less than 1 day to OpSi reset, OpsiCrossMonth is enabled'
                            'set OpsiMeowfficerFarming.ActionPointPreserve to 300 temporarily')
                return 300
            else:
                logger.info('Just less than 1 day to OpSi reset, '
                            'set ActionPointPreserve to 0 temporarily')
                return 0
        elif self.is_cl1_enabled and remain <= 2:
            logger.info('Just less than 3 days to OpSi reset, '
                        'set ActionPointPreserve to 1000 temporarily for hazard 1 leveling')
            return 1000
        elif remain <= 2:
            logger.info('Just less than 3 days to OpSi reset, '
                        'set ActionPointPreserve to 300 temporarily')
            return 300
        else:
            logger.info('Not close to OpSi reset')
            return 2000

    def handle_after_auto_search(self):
        logger.hr('After auto search', level=2)
        solved = False
        solved |= self.handle_fleet_emp_debuff()
        solved |= self.handle_fleet_repair(revert=False)
        logger.info(f'Handle after auto search finished, solved={solved}')
        return solved

    def cl1_ap_preserve(self):
        """
        Keeping enough startup AP to run CL1.
        """
        if self.is_cl1_enabled and get_os_reset_remain() > 2 \
                and self.get_yellow_coins() > self.config.OpsiHazard1Leveling_YellowCoinPreserve:
            logger.info('Keep 1000 AP when CL1 available')
            if not self.action_point_check(1000):
                self.config.opsi_task_delay(cl1_preserve=True)
                self.config.task_stop()

    _auto_search_battle_count = 0
    _auto_search_round_timer = 0
    _cl1_auto_search_battle_count = 0

    def on_auto_search_battle_count_reset(self):
        self._auto_search_battle_count = 0
        self._auto_search_round_timer = 0

    def on_auto_search_battle_count_add(self):
        self._auto_search_battle_count += 1
        logger.attr('battle_count', self._auto_search_battle_count)
        if getattr(self, 'is_in_task_cl1_leveling', False) and getattr(self, 'is_cl1_enabled', False):
            try:
                try:
                    self._cl1_auto_search_battle_count += 1
                except Exception:
                    self._cl1_auto_search_battle_count = 1
                logger.attr('cl1_battle_count', self._cl1_auto_search_battle_count)
                try:
                    self._cl1_increment_monthly(1)
                except Exception:
                    logger.debug('Failed to persist monthly CL1 battle increment', exc_info=True)
            except Exception:
                logger.debug('Failed to update cl1 battle counter', exc_info=True)

            if self._auto_search_battle_count % 2 == 1:
                if self._auto_search_round_timer:
                    cost = round(time.time() - self._auto_search_round_timer, 2)
                    logger.attr('CL1 time cost', f'{cost}s/round')
                self._auto_search_round_timer = time.time()

    def get_current_cl1_battle_count(self):
        return int(getattr(self, '_cl1_auto_search_battle_count', 0))

    def _cl1_month_key(self, year: int = None, month: int = None) -> str:
        from datetime import datetime
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        return f"{year:04d}-{month:02d}"

    def _cl1_monthly_file(self):
        from pathlib import Path
        project_root = Path(__file__).resolve().parents[2]
        cl1_dir = project_root / 'log' / 'cl1'
        try:
            cl1_dir.mkdir(parents=True, exist_ok=True)
            return cl1_dir / 'cl1_monthly.json'
        except Exception:
            log_dir = project_root / 'log'
            log_dir.mkdir(parents=True, exist_ok=True)
            return log_dir / 'cl1_monthly.json'

    def _load_cl1_monthly(self):
        import json
        f = self._cl1_monthly_file()
        if not f.exists():
            return {}
        try:
            with f.open('r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            logger.exception('Failed to load CL1 monthly file, resetting data')
            return {}

    def _save_cl1_monthly(self, data):
        import json
        f = self._cl1_monthly_file()
        try:
            with f.open('w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception('Failed to save CL1 monthly file')

    def _cl1_increment_monthly(self, delta: int = 1, year: int = None, month: int = None):
        key = self._cl1_month_key(year=year, month=month)
        data = self._load_cl1_monthly()
        try:
            data[key] = int(data.get(key, 0)) + int(delta)
        except Exception:
            data[key] = int(delta)
        self._save_cl1_monthly(data)

    def get_monthly_cl1_battle_count(self, year: int = None, month: int = None):
        key = self._cl1_month_key(year=year, month=month)
        data = self._load_cl1_monthly()
        return int(data.get(key, 0))

    def os_auto_search_daemon(self, drop=None, strategic=False, interrupt=None, skip_first_screenshot=True):
        """
        Args:
            drop (DropRecord):
            strategic (bool): True if running in strategic search
            skip_first_screenshot:

        Returns:
            int: Number of finished battle

        Raises:
            CampaignEnd: If auto search ended
            RequestHumanTakeover: If there's no auto search option.

        Pages:
            in: AUTO_SEARCH_OS_MAP_OPTION_OFF
            out: AUTO_SEARCH_OS_MAP_OPTION_OFF and info_bar_count() >= 2, if no more objects to clear on this map.
                 AUTO_SEARCH_REWARD if get auto search reward.
        """
        logger.hr('OS auto search', level=2)
        self.on_auto_search_battle_count_reset()
        unlock_checked = False
        unlock_check_timer = Timer(5, count=10).start()
        self.ash_popup_canceled = False

        success = True
        finished_combat = 0
        died_timer = Timer(1.5, count=3)
        self.hp_reset()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if not unlock_checked and unlock_check_timer.reached():
                logger.critical('Unable to use auto search in current zone')
                logger.critical('Please finish the story mode of OpSi to unlock auto search '
                                'before using any OpSi functions')
                raise RequestHumanTakeover
            if self.is_in_map():
                self.device.stuck_record_clear()
                if not success:
                    if died_timer.reached():
                        logger.warning('Fleet died confirm')
                        break
                else:
                    died_timer.reset()
            else:
                died_timer.reset()

            if not unlock_checked:
                if self.appear(AUTO_SEARCH_OS_MAP_OPTION_OFF, offset=(5, 120)):
                    unlock_checked = True
                elif self.appear(AUTO_SEARCH_OS_MAP_OPTION_OFF_DISABLED, offset=(5, 120)):
                    unlock_checked = True
                elif self.appear(AUTO_SEARCH_OS_MAP_OPTION_ON, offset=(5, 120)):
                    unlock_checked = True

            if self.handle_os_auto_search_map_option(
                    drop=drop,
                    enable=success
            ):
                unlock_checked = True
                continue
            if self.handle_retirement():
                # Retire will interrupt auto search, need a retry
                self.ash_popup_canceled = True
                continue
            if self.combat_appear():
                self.on_auto_search_battle_count_add()
                if strategic and self.config.task_switched():
                    self.interrupt_auto_search()
                result = self.auto_search_combat(drop=drop)
                if result:
                    finished_combat += 1
                else:
                    self.hp_get()
                    if any(self.need_repair):
                        success = False
                        logger.warning('Fleet died, stop auto search')
                        continue
            if self.handle_map_event():
                # Auto search can not handle siren searching device.
                continue

        return finished_combat

    def interrupt_auto_search(self, skip_first_screenshot=True):
        """
        Raises:
            TaskEnd: If auto search interrupted

        Pages:
            in: Any, usually to be is_combat_executing
            out: page_main
        """
        logger.info('Interrupting auto search')
        is_loading = False
        pause_interval = Timer(0.5, count=1)
        in_main_timer = Timer(3, count=6)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_in_main():
                logger.info('Auto search interrupted')
                self.config.task_stop()

            if self.appear_then_click(AUTO_SEARCH_REWARD, offset=(50, 50), interval=3):
                self.interval_clear(GOTO_MAIN)
                in_main_timer.reset()
                continue
            if pause_interval.reached():
                pause = self.is_combat_executing()
                if pause:
                    self.device.click(pause)
                    self.interval_reset(MAINTENANCE_ANNOUNCE)
                    is_loading = False
                    pause_interval.reset()
                    in_main_timer.reset()
                    continue
            if self.handle_combat_quit():
                self.interval_reset(MAINTENANCE_ANNOUNCE)
                pause_interval.reset()
                in_main_timer.reset()
                continue
            if self.appear_then_click(QUIT_RECONFIRM, offset=True, interval=5):
                self.interval_reset(MAINTENANCE_ANNOUNCE)
                pause_interval.reset()
                in_main_timer.reset()
                continue

            if self.appear_then_click(GOTO_MAIN, offset=(20, 20), interval=3):
                in_main_timer.reset()
                continue
            if self.ui_additional():
                continue
            if self.handle_map_event():
                continue
            # Only print once when detected
            if not is_loading:
                if self.is_combat_loading():
                    is_loading = True
                    in_main_timer.clear()
                    continue
                # Random background from page_main may trigger EXP_INFO_*, don't check them
                if in_main_timer.reached():
                    logger.info('handle_exp_info')
                    if self.handle_battle_status():
                        continue
                    if self.handle_exp_info():
                        continue
            elif self.is_combat_executing():
                is_loading = False
                in_main_timer.clear()
                continue

    def os_auto_search_run(self, drop=None, strategic=False):
        """
        Args:
            drop (DropRecord):
            strategic (bool): True to use strategic search

        Returns:
            int: Number of finished combat
        """
        finished_combat = 0
        for _ in range(5):
            backup = self.config.temporary(Campaign_UseAutoSearch=True)
            try:
                if strategic:
                    self.strategic_search_start(skip_first_screenshot=True)
                combat = self.os_auto_search_daemon(drop=drop, strategic=strategic)
                finished_combat += combat
            except CampaignEnd:
                logger.info('OS auto search finished')
            finally:
                backup.recover()

            # Continue if was Auto search interrupted by ash popup
            # Break if zone cleared
            if self.config.is_task_enabled('OpsiAshBeacon'):
                if self.handle_ash_beacon_attack() or self.ash_popup_canceled:
                    strategic = False
                    continue
                else:
                    break
            else:
                if self.info_bar_count() >= 2:
                    break
                elif self.ash_popup_canceled:
                    continue
                else:
                    break

        return finished_combat

    def clear_question(self, drop=None):
        """
        Clear nearly (and 3 grids from above) question marks on radar.
        Try 3 times at max to avoid loop tries on 2 adjacent fleet mechanism.

        Args:
            drop:

        Returns:
            bool: If cleared
        """
        logger.hr('Clear question', level=2)
        for _ in range(3):
            grid = self.radar.predict_question(self.device.image, in_port=self.zone.is_port)
            if grid is None:
                logger.info('No question mark above current fleet on this radar')
                return False

            logger.info(f'Found question mark on {grid}')
            self.handle_info_bar()

            self.update_os()
            self.view.predict()
            self.view.show()

            grid = self.convert_radar_to_local(grid)
            self.device.click(grid)
            with self.config.temporary(STORY_ALLOW_SKIP=False):
                result = self.wait_until_walk_stable(
                    drop=drop, walk_out_of_step=False, confirm_timer=Timer(1.5, count=4))
            if 'akashi' in result:
                self._solved_map_event.add('is_akashi')
                return True
            elif 'event' in result and grid.is_logging_tower:
                self._solved_map_event.add('is_logging_tower')
                return True
            elif 'event' in result and grid.is_scanning_device:
                self._solved_map_event.add('is_scanning_device')
                self.os_auto_search_run(drop=drop)
                return True
            else:
                logger.warning(f'Arrive question with unexpected result: {result}, expected: {grid.str}')
                continue

        logger.warning('Failed to goto question mark after 5 trail, '
                       'this might be 2 adjacent fleet mechanism, stopped')
        return False

    def run_auto_search(self, question=True, rescan=None, after_auto_search=True):
        """
        Clear current zone by running auto search.
        OpSi story mode must be cleared to unlock auto search.

        Args:
            question (bool):
                If clear nearing questions after auto search.
            rescan (bool, str): Whether to rescan the whole map after running auto search.
                This will clear siren scanning devices, siren logging tower,
                visit akashi's shop that auto search missed, and unlock mechanism that requires 2 fleets.
                Accept str also, `current` to scan current camera only,
                `full` to scan current then rescan the whole map

                This option should be disabled in special tasks like OpsiObscure, OpsiAbyssal, OpsiStronghold.
            after_auto_search (bool):
                Whether to call handle_after_auto_search() after auto search

        Returns:
            int: Number of finished combat
        """
        if rescan is None:
            rescan = self.config.OpsiGeneral_DoRandomMapEvent
        if rescan is True:
            rescan = 'full'
        self.handle_ash_beacon_attack()

        logger.info(f'Run auto search, question={question}, rescan={rescan}')
        finished_combat = 0
        with self.stat.new(
                genre=inflection.underscore(self.config.task.command),
                method=self.config.DropRecord_OpsiRecord
        ) as drop:
            while 1:
                combat = self.os_auto_search_run(drop)
                finished_combat += combat

                # Record current zone, skip this if no rewards from auto search.
                drop.add(self.device.image)

                self.hp_reset()
                self.hp_get()
                if after_auto_search:
                    if self.is_in_task_explore and not self.zone.is_port:
                        prev = self.zone
                        if self.handle_after_auto_search():
                            self.globe_goto(prev, types='DANGEROUS')
                            continue
                break

            # Rescan
            self._solved_map_event = set()
            self._solved_fleet_mechanism = False
            if question:
                self.clear_question(drop=drop)
            if rescan:
                self.map_rescan(rescan_mode=rescan, drop=drop)

            if drop.count == 1:
                drop.clear()

        return finished_combat

    _solved_map_event = set()
    _solved_fleet_mechanism = 0

    def run_strategic_search(self):
        self.handle_ash_beacon_attack()

        logger.hr('Run strategy search', level=2)
        self.os_auto_search_run(strategic=True)

        self.hp_reset()
        self.hp_get()
        self._solved_map_event = set()
        self._solved_fleet_mechanism = False
        self.clear_question()
        self.map_rescan()

    def map_rescan_current(self, drop=None):
        """

        Args:
            drop:

        Returns:
            bool: If solved a map random event
        """
        grids = self.view.select(is_exploration_reward=True)
        if 'is_exploration_reward' not in self._solved_map_event and grids and grids[0].is_exploration_reward:
            grid = grids[0]
            logger.info(f'Found exploration reward on {grid}')
            result = self.wait_until_walk_stable(drop=drop, walk_out_of_step=False, confirm_timer=Timer(1.5, count=4))
            if 'event' in result:
                self._solved_map_event.add('is_exploration_reward')
                return True
            else:
                return False

        grids = self.view.select(is_akashi=True)
        if 'is_akashi' not in self._solved_map_event and grids and grids[0].is_akashi:
            grid = grids[0]
            logger.info(f'Found Akashi on {grid}')
            fleet = self.convert_radar_to_local((0, 0))
            if fleet.distance_to(grid) > 1:
                self.device.click(grid)
                with self.config.temporary(STORY_ALLOW_SKIP=False):
                    result = self.wait_until_walk_stable(drop=drop, walk_out_of_step=False)
                if 'akashi' in result:
                    self._solved_map_event.add('is_akashi')
                    return True
                else:
                    return False
            else:
                logger.info(f'Akashi ({grid}) is near current fleet ({fleet})')
                self.handle_akashi_supply_buy(grid)
                self._solved_map_event.add('is_akashi')
                return True

        grids = self.view.select(is_scanning_device=True)
        if 'is_scanning_device' not in self._solved_map_event and grids and grids[0].is_scanning_device:
            grid = grids[0]
            logger.info(f'Found scanning device on {grid}')

            # 检查是否开启研究装置交互
            siren_research_enabled = False
            try:
                siren_research_enabled = bool(self.config.cross_get(
                    keys='OpsiHazard1Leveling.OpsiHazard1Leveling.SirenResearch_Enable',
                    default=False
                ))
            except Exception:
                logger.warning('Failed to get SirenResearch config, disabled by default')
                siren_research_enabled = False
            
            if not siren_research_enabled:
                logger.info('SirenResearch disabled by config, skip scanning device')
                self._solved_map_event.add('is_scanning_device')
                return True
                
            self.device.click(grid)
            with self.config.temporary(STORY_ALLOW_SKIP=False):
                result = self.wait_until_walk_stable(
                    drop=drop, walk_out_of_step=False, confirm_timer=Timer(1.5, count=4))
            self.os_auto_search_run(drop=drop)
            if 'event' in result:
                self._solved_map_event.add('is_scanning_device')
                return True
            else:
                return False

        grids = self.view.select(is_logging_tower=True)
        if 'is_logging_tower' not in self._solved_map_event and grids and grids[0].is_logging_tower:
            grid = grids[0]
            logger.info(f'Found logging tower on {grid}')
            self.device.click(grid)
            with self.config.temporary(STORY_ALLOW_SKIP=False):
                result = self.wait_until_walk_stable(
                    drop=drop, walk_out_of_step=False, confirm_timer=Timer(1.5, count=4))
            if 'event' in result:
                self._solved_map_event.add('is_logging_tower')
                return True
            else:
                return False

        grids = self.view.select(is_fleet_mechanism=True)
        if self.is_in_task_explore \
                and 'is_fleet_mechanism' not in self._solved_map_event \
                and grids \
                and grids[0].is_fleet_mechanism:
            grid = grids[0]
            logger.info(f'Found fleet mechanism on {grid}')
            self.device.click(grid)
            self.wait_until_walk_stable(drop=drop, walk_out_of_step=False, confirm_timer=Timer(1.5, count=4))

            if self._solved_fleet_mechanism:
                logger.info('All fleet mechanism are solved')
                self.os_auto_search_run(drop=drop)
                self._solved_map_event.add('is_fleet_mechanism')
                return True
            else:
                logger.info('One of the fleet mechanism is solved')
                self._solved_fleet_mechanism = True
                return True

        logger.info(f'No map event')
        return False

    def map_rescan_once(self, rescan_mode='full', drop=None):
        """
        Args:
            rescan_mode (str): `current` to scan current camera only,
                `full` to scan current then rescan the whole map
            drop:

        Returns:
            bool: If solved a map random event
        """
        result = False

        # Try current camera first
        logger.hr('Map rescan current', level=2)
        self.map_data_init(map_=None)
        self.handle_info_bar()
        self.update()
        if self.map_rescan_current(drop=drop):
            logger.info(f'Map rescan once end, result={True}')
            return True

        if rescan_mode == 'full':
            logger.hr('Map rescan full', level=2)
            self.map_init(map_=None)
            queue = self.map.camera_data
            while len(queue) > 0:
                logger.hr(f'Map rescan {queue[0]}')
                queue = queue.sort_by_camera_distance(self.camera)
                self.focus_to(queue[0], swipe_limit=(6, 5))
                self.focus_to_grid_center(0.3)

                if self.map_rescan_current(drop=drop):
                    result = True
                    break
                queue = queue[1:]

        logger.info(f'Map rescan once end, result={result}')
        return result

    def map_rescan(self, rescan_mode='full', drop=None):
        if self.zone.is_port:
            logger.info('Current zone is a port, do not need rescan')
            return False

        for _ in range(5):
            if not self._solved_fleet_mechanism:
                self.fleet_set(self.config.OpsiFleet_Fleet)
            else:
                self.fleet_set(self.get_second_fleet())
            if not self.is_in_task_explore and len(self._solved_map_event):
                logger.info('Solved a map event and not in OpsiExplore, stop rescan')
                logger.attr('Solved_map_event', self._solved_map_event)
                self.fleet_set(self.config.OpsiFleet_Fleet)
                return False
            result = self.map_rescan_once(rescan_mode=rescan_mode, drop=drop)
            if not result:
                logger.attr('Solved_map_event', self._solved_map_event)
                self.fleet_set(self.config.OpsiFleet_Fleet)
                return True

        logger.attr('Solved_map_event', self._solved_map_event)
        logger.warning('Too many trial on map rescan, stop')
        self.fleet_set(self.config.OpsiFleet_Fleet)
        return False

    def safe_swipe(self, start, end, duration=0.5, retries=2):
        """
        在多次滑动/重试场景下的安全滑动：
         - 清理设备的卡住/点击历史记录
         - 使用较长时长与间隔，减少被识别为无效滑动的概率
        """
        for attempt in range(1, retries + 1):
            try:
                try:
                    self.device.stuck_record_clear()
                except Exception:
                    pass
                self.device.swipe(start, end, duration=duration)
                time.sleep(0.45)
                return True
            except Exception as e:
                logger.warning(f'safe_swipe attempt {attempt} failed: {e}')
                time.sleep(0.4)
                continue
        return False

    # 基于ShaddockNH3极致侵蚀一的个人修改
    def _execute_fixed_patrol_scan(self, ExecuteFixedPatrolScan: bool = False, **kwargs):
        """
        该函数在指挥每支舰队移动前，都会先执行一次强制的视角复位，
        然后精确指挥1-4号舰队前往预设的网格坐标，并在所有舰队
        移动到位后，执行一次清除了残留状态的全图扫描。
        """
        logger.hr('执行定点巡逻扫描')

        if not ExecuteFixedPatrolScan:
            logger.info('ExecuteFixedPatrolScan 未启用，跳过定点巡逻。')
            return
        logger.attr('ExecuteFixedPatrolScan', True)

        self.map_init(map_=None)
        if not hasattr(self, 'map') or not self.map.grids:
            logger.warning('无法获取当前地图网格数据，已跳过定点巡逻。')
            return

        solved = getattr(self, '_solved_map_event', set())
        if any(k in solved for k in ('is_akashi', 'is_scanning_device', 'is_logging_tower')):
            logger.info('彩蛋：雪风大人保佑你，本次舰队移动已跳过')
            return

        patrol_locations = [(2, 0), (3, 0), (4, 0), (5, 0)]  # 对应 C1, D1, E1, F1

        for i, target_loc in enumerate(patrol_locations):
            fleet_index = i + 1

            target_grid_group = self.map.select(location=target_loc)
            if not target_grid_group:
                logger.warning(f'在地图上找不到坐标为 {target_loc} 的格子，跳过舰队 {fleet_index} 的移动。')
                continue
            target_grid = target_grid_group[0]

            logger.hr(f'定点巡逻: 指挥舰队 {fleet_index} 前往 {target_grid}', level=2)

            self.fleet_set(fleet_index)

            logger.info('执行视角复位，强制滑动到地图顶端...')

            top_point = (640, 150)
            bottom_point = (640, 600)
            quick_ok = True
            try:
                for _ in range(2):
                    self.device.swipe(top_point, bottom_point, duration=0.3)
                    time.sleep(0.18)
            except Exception:
                quick_ok = False
                logger.debug('快速滑动复位遇到异常，尝试安全滑动')

            if not quick_ok:
                if not self.safe_swipe(top_point, bottom_point, duration=0.55, retries=2):
                    logger.warning('视角复位（安全滑动）失败，继续尝试下一步')
                else:
                    logger.info('视角复位（安全滑动）完成。')
            else:
                logger.info('快速滑动复位完成。')
            time.sleep(0.45)

            self.focus_to(target_grid.location)
            self.update()
            clickable_grid_group = self.view.select(location=target_loc)
            if not clickable_grid_group:
                logger.warning(f'已将视角移动到 {target_loc}，但在视野中找不到可点击的格子。')
                continue

            for try_idx in range(2):
                try:
                    try:
                        self.device.stuck_record_clear()
                    except Exception:
                        pass
                    time.sleep(0.1)
                    self.device.click(clickable_grid_group[0])
                    self.wait_until_walk_stable(confirm_timer=Timer(1.5, count=4))
                    logger.info(f'舰队 {fleet_index} 已到达 {target_grid}。')
                    break
                except (MapWalkError, GameTooManyClickError) as e:
                    logger.warning(f'舰队移动异常: {e}，尝试强制恢复（{try_idx + 1}/2）')
                    recovered = False
                    try:
                        recovered = self._force_move_recover(target_zone=self.zone if self.zone else None)
                    except Exception:
                        recovered = False
                    if recovered:
                        time.sleep(0.5)
                        continue
                    logger.warning('尝试软恢复（back / screenshot / rebuild view）')
                    try:
                        for _ in range(3):
                            try:
                                self.device.back()
                            except Exception:
                                pass
                            time.sleep(0.25)
                        self.device.screenshot()
                        try:
                            self.ui_ensure(page_os)
                            self.map_init(map_=None)
                            self.update()
                        except Exception:
                            logger.debug('重建视图失败（soft recovery）', exc_info=True)
                        clickable_grid_group = self.view.select(location=target_loc)
                        if clickable_grid_group:
                            logger.info('软恢复后找到格子，重试点击')
                            try:
                                time.sleep(0.3)
                                self.device.click(clickable_grid_group[0])
                                self.wait_until_walk_stable(confirm_timer=Timer(1.5, count=4))
                                logger.info('软恢复成功，舰队已到达')
                                break
                            except Exception:
                                logger.debug('软恢复重试点击失败', exc_info=True)
                    except Exception as rec_e:
                        logger.debug(f'软恢复过程出现异常: {rec_e}')
                    if try_idx == 1:
                        logger.warning('软恢复失败，尝试重启应用以恢复状态')
                        try:
                            self.device.app_stop()
                            time.sleep(1.0)
                            self.device.app_start()
                            LoginHandler(self.config, self.device).handle_app_login()
                            # 确保回到 OS 页并重建地图数据
                            self.ui_ensure(page_os)
                            time.sleep(0.8)
                            try:
                                self.map_init(map_=None)
                                self.update()
                            except Exception:
                                logger.debug('重建地图数据失败（app restart）', exc_info=True)
                            clickable_grid_group = self.view.select(location=target_loc)
                            if clickable_grid_group:
                                time.sleep(0.3)
                                self.device.click(clickable_grid_group[0])
                                self.wait_until_walk_stable(confirm_timer=Timer(1.5, count=4))
                                logger.info('重启应用后恢复成功，舰队已到达')
                                break
                        except Exception:
                            logger.error('应用重启恢复失败，跳过该舰队并继续下一步', exc_info=True)
                            # 不要求人工接管，继续后续流程（让外层循环接着处理下一个舰队或最终重扫）
                            recovered = False
                    time.sleep(0.5)

        backup = self.config.temporary(OpsiGeneral_RepairThreshold=-1, Campaign_UseAutoSearch=False)
        try:
            logger.info('所有舰队已定点，执行最终全图重扫（双遍检查）')
            self._solved_map_event = set()
            for _ in range(2):
                try:
                    self.map_rescan(rescan_mode='full')
                except Exception as e:
                    logger.debug(f'最终全图重扫出现异常，继续重试: {e}', exc_info=True)
                    time.sleep(0.6)
        finally:
            backup.recover()

        logger.info('执行一次自律寻敌以清理可能的装置')
        try:
            self.run_auto_search(question=True, rescan=None, after_auto_search=True)
        except Exception as e:
            logger.warning(f'自律寻敌过程出现异常: {e}')            