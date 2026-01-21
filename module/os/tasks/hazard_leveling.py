from module.logger import logger
from module.os.map import OSMap


class OpsiHazard1Leveling(OSMap):
    def os_hazard1_leveling(self):
        logger.hr('OS hazard 1 leveling', level=1)
        # Without these enabled, CL1 gains 0 profits
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=True,
        )
        #if not self.config.is_task_enabled('OpsiMeowfficerFarming'):
        #    self.config.cross_set(keys='OpsiMeowfficerFarming.Scheduler.Enable', value=True)
        while True:
            try:
                self.config.OS_ACTION_POINT_PRESERVE = int(self.config.cross_get(
                    keys='OpsiHazard1Leveling.OpsiHazard1Leveling.MinimumActionPointReserve',
                    default=200
                ))
            except Exception:
                self.config.OS_ACTION_POINT_PRESERVE = 200
            if self.config.is_task_enabled('OpsiAshBeacon') \
                    and not self._ash_fully_collected \
                    and self.config.OpsiAshBeacon_EnsureFullyCollected:
                logger.info('Ash beacon not fully collected, ignore action point limit temporarily')
                self.config.OS_ACTION_POINT_PRESERVE = 0
            logger.attr('OS_ACTION_POINT_PRESERVE', self.config.OS_ACTION_POINT_PRESERVE)

            if self.get_yellow_coins() < self.config.OpsiHazard1Leveling_YellowCoinPreserve:
                logger.info(f'Reach the limit of yellow coins, preserve={self.config.OpsiHazard1Leveling_YellowCoinPreserve}')
                with self.config.multi_set():
                    self.config.task_delay(server_update=True)
                #    if not self.is_in_opsi_explore():
                #        self.config.task_call('OpsiMeowfficerFarming')
                self.config.task_stop()

            self.get_current_zone()

            # Preset action point to 70
            # When running CL1 oil is for running CL1, not meowfficer farming
            keep_current_ap = True
            if self.config.OpsiGeneral_BuyActionPointLimit > 0:
                keep_current_ap = False
            self.action_point_set(cost=120, keep_current_ap=keep_current_ap, check_rest_ap=True)
            #if self._action_point_total >= 3000:
            #    with self.config.multi_set():
            #        self.config.task_delay(server_update=True)
            #        if not self.is_in_opsi_explore():
            #            self.config.task_call('OpsiMeowfficerFarming')
            #    self.config.task_stop()

            if self.config.OpsiHazard1Leveling_TargetZone != 0:
                zone = self.config.OpsiHazard1Leveling_TargetZone
            else:
                zone = 22
            logger.hr(f'OS hazard 1 leveling, zone_id={zone}', level=1)
            if self.zone.zone_id != zone or not self.is_zone_name_hidden:
                self.globe_goto(self.name_to_zone(zone), types='SAFE', refresh=True)
            self.fleet_set(self.config.OpsiFleet_Fleet)
            self.run_strategic_search()

            if self.config.OpsiHazard1Leveling_ExecuteFixedPatrolScan:
                exec_fixed = getattr(self.config, 'OpsiHazard1Leveling_ExecuteFixedPatrolScan', False)
                if exec_fixed:
                    self._execute_fixed_patrol_scan(ExecuteFixedPatrolScan=True)

            self.handle_after_auto_search()
            solved_events = getattr(self, '_solved_map_event', set())
            if 'is_akashi' in solved_events:
                try:
                    from datetime import datetime
                    key = f"{datetime.now():%Y-%m}-akashi"
                    data = self._load_cl1_monthly()
                    data[key] = int(data.get(key, 0)) + 1
                    self._save_cl1_monthly(data)
                    logger.attr('cl1_akashi_monthly', data[key])
                except Exception:
                    logger.exception('Failed to persist CL1 akashi monthly count')

            self.config.check_task_switch()