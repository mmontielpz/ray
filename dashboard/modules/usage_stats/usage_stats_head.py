import asyncio
import logging
import random

import ray

import ray.dashboard.utils as dashboard_utils
import ray._private.usage.usage_lib as ray_usage_lib

from ray.dashboard.utils import async_loop_forever

logger = logging.getLogger(__name__)


class UsageStatsHead(dashboard_utils.DashboardHeadModule):
    def __init__(self, dashboard_head):
        super().__init__(dashboard_head)
        self.cluster_metadata = ray_usage_lib.get_cluster_metadata(
            ray.experimental.internal_kv.internal_kv_get_gcs_client(),
            num_retries=20,
        )
        self.session_dir = dashboard_head.session_dir
        self.client = ray_usage_lib.UsageReportClient()
        # The total number of report succeeded.
        self.total_success = 0
        # The total number of report failed.
        self.total_failed = 0
        # The seq number of report. It increments whenever a new report is sent.
        self.seq_no = 0

    async def _report_usage(self):
        if not ray_usage_lib._usage_stats_enabled():
            return

        """
        - Always write usage_stats.json regardless of report success/failure.
        - If report fails, the error message should be written to usage_stats.json
        - If file write fails, the error will just stay at dashboard.log.
            usage_stats.json won't be written.
        """
        try:
            data = ray_usage_lib.generate_report_data(
                self.cluster_metadata,
                self.total_success,
                self.total_failed,
                self.seq_no,
            )
            error = None
            try:
                await self.client.report_usage_data_async(
                    ray_usage_lib._usage_stats_report_url(), data
                )
            except Exception as e:
                logger.info(f"Usage report request failed. {e}")
                error = str(e)
                self.total_failed += 1
            else:
                self.total_success += 1
            finally:
                self.seq_no += 1

            data = ray_usage_lib.generate_write_data(data, error)
            await self.client.write_usage_data_async(data, self.session_dir)

        except Exception as e:
            logger.exception(e)
            logger.info(f"Usage report failed: {e}")

    @async_loop_forever(ray_usage_lib._usage_stats_report_interval_s())
    async def periodically_report_usage(self):
        await self._report_usage()

    async def run(self, server):
        if not ray_usage_lib._usage_stats_enabled():
            logger.info("Usage reporting is disabled.")
            return
        else:
            logger.info("Usage reporting is enabled.")
            await self._report_usage()
            # Add a random offset before the first report to remove sample bias.
            await asyncio.sleep(
                random.randint(0, ray_usage_lib._usage_stats_report_interval_s())
            )
            await asyncio.gather(self.periodically_report_usage())

    @staticmethod
    def is_minimal_module():
        return True
