"""兼容 shim：knowledge_navigation.logger → 标准 logging"""
import logging


def get_logger(name: str = "knowledge_navigation") -> logging.Logger:
    """提供旧的 logger 接口兼容。"""
    return logging.getLogger(name)
