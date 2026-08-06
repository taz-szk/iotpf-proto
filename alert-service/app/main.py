from apscheduler.schedulers.blocking import BlockingScheduler
from app.config import settings
from app.scheduler import evaluate_all_tenants
import logging

logging.basicConfig(level=logging.INFO)

def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(
        evaluate_all_tenants,
        trigger="interval",
        seconds=settings.eval_interval_sec,
        id="alert_eval",
        max_instances=1,
        misfire_grace_time=30,
    )
    print(f"Alert Service starting (interval={settings.eval_interval_sec}s)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
