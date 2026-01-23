from module.logger import logger
from module.os.map import OSMap
from module.base.timer import Timer

class OpsiScanningDeviceFarming(OSMap):
    def os_scanningdevice_farming(self):
        """
        Go to the designated area for scanning_device farming and end the task once found
        by Forven47 2026.01.23
        """
        farming_zone = int(self.config.cross_get(
            keys='OpsiScanningDeviceFarming.OpsiScanningDeviceFarming.TargetZone',
            default=0))
        hazard1_zone = int(self.config.cross_get(
            keys='OpsiHazard1Leveling.OpsiHazard1Leveling.TargetZone',
            default=0))
        preserveac = int(self.config.cross_get(
            keys='OpsiScanningDeviceFarming.OpsiScanningDeviceFarming.ActionPointPreserve',
            default=500))
        disabled_auto_search = bool(self.config.cross_get(
            keys='OpsiHazard1Leveling.OpsiHazard1Leveling.BugZoneAutoSearch_Disable',
            default=True))
        preserve = min(self.get_action_point_limit(), preserveac, 2000)
        ap_checked = False

        logger.hr(f'OS scanningdevice farming, zone_id={farming_zone}', level=1)

        if disabled_auto_search:
            self.config.cross_set(
                keys='OpsiHazard1Leveling.OpsiHazard1Leveling.BugZoneAutoSearch_Disable',
                value=False)
        
        if self.is_in_opsi_explore():
            logger.warning(f'OpsiExplore is still running, cannot do {self.config.task.command}')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
            
        if preserve == 0:
            self.config.override(OpsiFleet_Submarine=False)

        while True:
            self.config.OS_ACTION_POINT_PRESERVE = preserve
            if not ap_checked:
                keep_current_ap = True
                check_rest_ap = True
                self.action_point_set(cost=0, keep_current_ap=keep_current_ap, check_rest_ap=check_rest_ap)
                ap_checked = True

            if farming_zone != 0:
                logger.hr(f'OS scanningdevice farming, zone_id={farming_zone}', level=1)
                self.globe_goto(farming_zone, refresh=True)
                self.fleet_set(self.config.OpsiFleet_Fleet)
                self.os_order_execute(
                    recon_scan=False,
                    submarine_call=self.config.OpsiFleet_Submarine)
                device_found = False
                self.zone_init()
                self.map_init(map_=None)
                camera_queue = self.map.camera_data
                find_device_timer = Timer(30, count=1).start()
                while not find_device_timer.reached() and not device_found:
                    if len(camera_queue) == 0:
                        camera_queue = self.map.camera_data
                    camera_queue = camera_queue.sort_by_camera_distance(self.camera)
                    target_camera = camera_queue[0]
                    camera_queue = camera_queue[1:]
                    self.focus_to(target_camera, swipe_limit=(6, 5))
                    self.focus_to_grid_center(0.3)
                    self.device.screenshot()
                    self.update()
                    grids = self.view.select(is_scanning_device=True)
                    if grids and grids[0].is_scanning_device:
                        logger.info(f'Found scanning device on grid {grids[0]}')
                        device_found = True
                if device_found:
                    self.globe_goto(hazard1_zone, refresh=True)
                    self.config.cross_set(
                        keys='OpsiScanningDeviceFarming.Scheduler.Enable',
                        value=False)
                    self.config.cross_set(
                        keys='OpsiHazard1Leveling.OpsiHazard1Leveling.BugZoneAutoSearch_Disable', 
                        value=True)
                    self.config.cross_set(
                        keys='OpsiHazard1Leveling.OpsiHazard1Leveling.SirenBug_Enable', 
                        value=True)
                    self.config.cross_set(
                        keys='OpsiHazard1Leveling.OpsiHazard1Leveling.SirenBug_Zone', 
                        value=farming_zone)
                    self.config.check_task_switch()
                else:
                    self.run_auto_search()
                    self.handle_after_auto_search()
                    self.config.check_task_switch()
            else:
                logger.info('target_zone is 0')
                self.globe_goto(hazard1_zone, refresh=True)
                self.config.cross_set(
                    keys='OpsiScanningDeviceFarming.Scheduler.Enable',
                    value=False)
                self.config.cross_set(
                    keys='OpsiHazard1Leveling.OpsiHazard1Leveling.BugZoneAutoSearch_Disable', 
                    value=True)
                self.config.check_task_switch()

