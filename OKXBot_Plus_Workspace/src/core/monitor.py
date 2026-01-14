import time
import psutil
import logging
from datetime import datetime

class HealthMonitor:
    """系统健康状态监控器"""
    def __init__(self):
        self.logger = logging.getLogger("crypto_oracle")
        self.start_time = time.time()
        self.api_calls = {
            'okx': {'total': 0, 'failed': 0},
            'deepseek': {'total': 0, 'failed': 0}
        }
        self.trade_executions = {
            'total': 0,
            'successful': 0,
            'failed': 0
        }
        self.system_metrics = {}
    
    def record_api_call(self, provider, success=True):
        """记录API调用"""
        self.api_calls[provider]['total'] += 1
        if not success:
            self.api_calls[provider]['failed'] += 1
    
    def record_trade_execution(self, success=True):
        """记录交易执行"""
        self.trade_executions['total'] += 1
        if success:
            self.trade_executions['successful'] += 1
        else:
            self.trade_executions['failed'] += 1
    
    def collect_system_metrics(self):
        """收集系统指标"""
        try:
            # CPU 使用率
            cpu_usage = psutil.cpu_percent(interval=0.1)
            
            # 内存使用情况
            memory = psutil.virtual_memory()
            memory_usage = memory.percent
            memory_used = memory.used / (1024 * 1024 * 1024)  # GB
            memory_total = memory.total / (1024 * 1024 * 1024)  # GB
            
            # 磁盘使用情况
            disk = psutil.disk_usage('/')
            disk_usage = disk.percent
            
            # 网络情况
            network = psutil.net_io_counters()
            bytes_sent = network.bytes_sent / (1024 * 1024)  # MB
            bytes_recv = network.bytes_recv / (1024 * 1024)  # MB
            
            # 系统负载
            load_avg = psutil.getloadavg() if hasattr(psutil, 'getloadavg') else (0, 0, 0)
            
            self.system_metrics = {
                'cpu_usage': cpu_usage,
                'memory_usage': memory_usage,
                'memory_used': memory_used,
                'memory_total': memory_total,
                'disk_usage': disk_usage,
                'bytes_sent': bytes_sent,
                'bytes_recv': bytes_recv,
                'load_avg': load_avg,
                'uptime': time.time() - self.start_time
            }
        except Exception as e:
            self.logger.error(f"收集系统指标失败: {e}")
    
    def get_health_report(self):
        """获取健康报告"""
        self.collect_system_metrics()
        
        uptime = self.system_metrics.get('uptime', 0)
        uptime_str = self._format_uptime(uptime)
        
        report = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'uptime': uptime_str,
            'system_metrics': self.system_metrics,
            'api_calls': self.api_calls,
            'trade_executions': self.trade_executions,
            'health_status': self._assess_health_status()
        }
        
        return report
    
    def _format_uptime(self, seconds):
        """格式化运行时间"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours}h {minutes}m {seconds}s"
    
    def _assess_health_status(self):
        """评估健康状态"""
        # 基于系统指标和API调用情况评估健康状态
        status = "HEALTHY"
        issues = []
        
        # 检查系统资源使用情况
        if self.system_metrics.get('cpu_usage', 0) > 80:
            issues.append(f"CPU 使用率过高: {self.system_metrics['cpu_usage']}%")
            status = "WARNING"
        
        if self.system_metrics.get('memory_usage', 0) > 80:
            issues.append(f"内存使用率过高: {self.system_metrics['memory_usage']}%")
            status = "WARNING"
        
        if self.system_metrics.get('disk_usage', 0) > 90:
            issues.append(f"磁盘使用率过高: {self.system_metrics['disk_usage']}%")
            status = "CRITICAL"
        
        # 检查 API 调用失败率
        for provider, stats in self.api_calls.items():
            if stats['total'] > 0:
                failure_rate = (stats['failed'] / stats['total']) * 100
                if failure_rate > 30:
                    issues.append(f"{provider} API 失败率过高: {failure_rate:.1f}%")
                    status = "CRITICAL"
        
        # 检查交易执行失败率
        if self.trade_executions['total'] > 0:
            failure_rate = (self.trade_executions['failed'] / self.trade_executions['total']) * 100
            if failure_rate > 20:
                issues.append(f"交易执行失败率过高: {failure_rate:.1f}%")
                status = "WARNING"
        
        return {
            'status': status,
            'issues': issues
        }
    
    def log_health_report(self):
        """记录健康报告"""
        report = self.get_health_report()
        
        self.logger.info("=" * 80)
        self.logger.info("🏥 系统健康状态报告")
        self.logger.info(f"📅 时间: {report['timestamp']}")
        self.logger.info(f"⏰ 运行时间: {report['uptime']}")
        self.logger.info("-" * 80)
        
        # 系统指标
        self.logger.info("📊 系统指标:")
        metrics = report['system_metrics']
        self.logger.info(f"   CPU 使用率: {metrics.get('cpu_usage', 0):.1f}%")
        self.logger.info(f"   内存使用率: {metrics.get('memory_usage', 0):.1f}% ({metrics.get('memory_used', 0):.1f}GB / {metrics.get('memory_total', 0):.1f}GB)")
        self.logger.info(f"   磁盘使用率: {metrics.get('disk_usage', 0):.1f}%")
        self.logger.info(f"   网络发送: {metrics.get('bytes_sent', 0):.1f}MB | 接收: {metrics.get('bytes_recv', 0):.1f}MB")
        
        # API 调用统计
        self.logger.info("-" * 80)
        self.logger.info("🌐 API 调用统计:")
        for provider, stats in report['api_calls'].items():
            if stats['total'] > 0:
                success_rate = ((stats['total'] - stats['failed']) / stats['total']) * 100
                self.logger.info(f"   {provider}: 总计 {stats['total']}, 成功 {stats['total'] - stats['failed']}, 失败 {stats['failed']} ({success_rate:.1f}% 成功率)")
            else:
                self.logger.info(f"   {provider}: 无调用")
        
        # 交易执行统计
        self.logger.info("-" * 80)
        self.logger.info("💹 交易执行统计:")
        executions = report['trade_executions']
        if executions['total'] > 0:
            success_rate = (executions['successful'] / executions['total']) * 100
            self.logger.info(f"   总计: {executions['total']}, 成功: {executions['successful']}, 失败: {executions['failed']} ({success_rate:.1f}% 成功率)")
        else:
            self.logger.info("   无交易执行")
        
        # 健康状态
        self.logger.info("-" * 80)
        health_status = report['health_status']
        self.logger.info(f"🚦 健康状态: {health_status['status']}")
        if health_status['issues']:
            self.logger.warning("⚠️  问题:")
            for issue in health_status['issues']:
                self.logger.warning(f"   - {issue}")
        else:
            self.logger.info("✅ 系统运行正常")
        
        self.logger.info("=" * 80)

# 创建全局健康监控实例
health_monitor = HealthMonitor()