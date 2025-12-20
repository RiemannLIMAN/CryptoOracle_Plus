import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import sys
import logging

# [新增] 在绘图模块内部也屏蔽字体警告，防止单独运行时刷屏
logging.getLogger("matplotlib").setLevel(logging.ERROR)
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

# 设置中文字体 (优先尝试 Linux 常见字体，然后是 Windows 字体)
# 注意：图表主要为英文，使用 DejaVu Sans 即可避免 "Generic family not found" 警告
plt.rcParams['font.sans-serif'] = [
    'DejaVu Sans',      # Linux/Docker 默认
    'Liberation Sans',  # Linux
    'Arial',            # Windows
    'SimHei',           # Windows 中文
    'Microsoft YaHei',  # Windows 中文
    'SimSun',           # Windows 中文
    'sans-serif'        # 兜底
]
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

def generate_pnl_chart(csv_path=None, output_path=None, verbose=True):
    """
    读取 PnL 历史数据并生成折线图
    """
    # 智能推断路径
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    if csv_path is None:
        csv_path = os.path.join(project_root, "data", "pnl_history.csv")
        # 回退兼容：如果 data 下没有，但根目录有，则使用根目录的 (迁移过渡期)
        if not os.path.exists(csv_path) and os.path.exists(os.path.join(project_root, "pnl_history.csv")):
             csv_path = os.path.join(project_root, "pnl_history.csv")

    if output_path is None:
        output_path = os.path.join(project_root, "png", "pnl_chart.png")

    if not os.path.exists(csv_path):
        if verbose:
            print(f"错误: 找不到文件 {csv_path}")
        return

    try:
        # 读取 CSV
        df = pd.read_csv(csv_path)
        
        # 转换时间戳
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 按时间排序
        df = df.sort_values('timestamp')

        if df.empty:
            print("警告: 数据为空，无法生成图表")
            return

        # 创建画布 (2个子图)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
        plt.subplots_adjust(hspace=0.3)

        # === 子图 1: 总权益曲线 ===
        ax1.plot(df['timestamp'], df['total_equity'], color='#1f77b4', linewidth=2, label='Total Equity (USDT)')
        ax1.set_title('CryptoOracle Account Equity Curve', fontsize=14, fontweight='bold')
        ax1.set_ylabel('Total Equity (USDT)', fontsize=12)
        ax1.grid(True, linestyle='--', alpha=0.7)
        ax1.legend(loc='upper left')
        
        # 标记最大值和最小值
        max_eq = df['total_equity'].max()
        min_eq = df['total_equity'].min()
        ax1.axhline(y=max_eq, color='green', linestyle=':', alpha=0.5)
        ax1.axhline(y=min_eq, color='red', linestyle=':', alpha=0.5)

        # === 子图 2: 盈亏百分比 ===
        # 根据盈亏正负设置颜色
        colors = ['red' if x < 0 else 'green' for x in df['pnl_percent']]
        
        # 使用 plot 绘制连线，使用 scatter 绘制点
        ax2.plot(df['timestamp'], df['pnl_percent'], color='gray', alpha=0.5, linewidth=1)
        sc = ax2.scatter(df['timestamp'], df['pnl_percent'], c=colors, s=20, label='PnL Rate (%)')
        
        ax2.set_title('Cumulative PnL Rate (%)', fontsize=14, fontweight='bold')
        ax2.set_ylabel('PnL Rate (%)', fontsize=12)
        ax2.set_xlabel('Time', fontsize=12)
        ax2.grid(True, linestyle='--', alpha=0.7)
        
        # 添加 0% 基准线
        ax2.axhline(y=0, color='black', linestyle='-', linewidth=1)

        # === 格式化 X 轴时间 ===
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        plt.gcf().autofmt_xdate()  # 自动旋转日期标签

        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # 保存图片
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        if verbose:
            print(f"✅ 图表已生成并保存至: {output_path}")
        
        # 如果是在本地环境，可以尝试显示 (可选)
        # plt.show()
        
        plt.close()

    except Exception as e:
        print(f"生成图表时发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    generate_pnl_chart()
