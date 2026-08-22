import logging


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "authorization"):
            record.authorization = "[REDACTED]"
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s message=%(message)s")
