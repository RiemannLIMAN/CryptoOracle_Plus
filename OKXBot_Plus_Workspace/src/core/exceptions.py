class CryptoOracleException(Exception):
    """基础异常类"""
    pass

class APIConnectionError(CryptoOracleException):
    """API连接异常"""
    pass

class APIResponseError(CryptoOracleException):
    """API响应异常"""
    pass

class ConfigError(CryptoOracleException):
    """配置异常"""
    pass

class TradingError(CryptoOracleException):
    """交易异常"""
    pass

class RiskManagementError(CryptoOracleException):
    """风险管理异常"""
    pass

class DataProcessingError(CryptoOracleException):
    """数据处理异常"""
    pass

class AIError(CryptoOracleException):
    """AI分析异常"""
    pass
