import requests
from bs4 import BeautifulSoup
import json
import time
import os
import subprocess
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================== 配置 ==================
CSV_FILE = "treasure线下签售.csv"                # CSV 文件名
GITHUB_REPO = "Juineii/treasure_k40509"        # 请替换为您的仓库名
GITHUB_BRANCH = "main"                          # 分支名（main 或 master）
# GitHub Personal Access Token 优先从环境变量 GITHUB_TOKEN 读取

# 商品URL和地址名称映射
product_urls = {
    "https://www.ktown4u.com/iteminfo?eve_no=44276513&goods_no=161729&grp_no=44276516": "red国际地址",
    "https://cn.ktown4u.com/iteminfo?eve_no=44276514&goods_no=161920&grp_no=44276517": "red中国地址",
    "https://kr.ktown4u.com/iteminfo?eve_no=44276513&goods_no=161729&grp_no=44276516": "red韩国地址",
    "https://www.ktown4u.com/iteminfo?eve_no=44276513&goods_no=161730&grp_no=44276516": "ice国际地址",
    "https://cn.ktown4u.com/iteminfo?eve_no=44276514&goods_no=161922&grp_no=44276517": "ice中国地址",
    "https://kr.ktown4u.com/iteminfo?eve_no=44276513&goods_no=161730&grp_no=44276516": "ice韩国地址"
}

# 存储库存数据
last_quantities = {}
initial_stock_printed = {}  # 记录初始库存是否已打印


# ================== Git 推送函数 ==================
def git_push_update():
    """
    将最新的 CSV 文件提交并推送到 GitHub
    """
    try:
        # 获取 GitHub Token（优先从环境变量读取）
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            print("⚠️ 环境变量 GITHUB_TOKEN 未设置，跳过 Git 推送")
            return

        # 构建带认证的远程仓库 URL
        remote_url = f"https://{token}@github.com/{GITHUB_REPO}.git"

        # 添加 CSV 文件到暂存区
        subprocess.run(['git', 'add', CSV_FILE], check=True, capture_output=True)

        # 检查是否有文件变化（避免空提交）
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True)
        if result.returncode != 0:
            # 有变化，提交
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            commit_msg = f"自动更新数据 {timestamp}"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True, capture_output=True)

            # 推送到 GitHub（指定分支）
            subprocess.run(
                ['git', 'push', remote_url, f'HEAD:{GITHUB_BRANCH}'],
                check=True,
                capture_output=True,
                text=True
            )
            print(f"✅ 已推送到 GitHub: {commit_msg}")
        else:
            print("⏭️ CSV 文件无变化，跳过推送")

    except subprocess.CalledProcessError as e:
        print(f"❌ Git 操作失败: {e.stderr if e.stderr else e}")
    except Exception as e:
        print(f"❌ 推送过程中发生错误: {e}")


def create_session():
    """
    创建带有重试机制和请求头的请求会话
    """
    session = requests.Session()
    # 设置请求头，模拟浏览器
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",  # Do Not Track
        "Upgrade-Insecure-Requests": "1"
    }
    session.headers.update(headers)

    # 设置重试机制
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_stock_data(url, session):
    """
    从指定URL获取库存数据
    """
    try:
        # 发送HTTP请求
        response = session.get(url, timeout=5)
        response.raise_for_status()  # 抛出HTTP错误

        # 解析HTML
        soup = BeautifulSoup(response.text, "html.parser")
        script_tag = soup.find("script", {"id": "__NEXT_DATA__"})

        if not script_tag:
            return None

        # 解析JSON数据
        try:
            json_data = json.loads(script_tag.string)
        except json.JSONDecodeError:
            return None

        # 提取productDetails
        try:
            page_props = json_data.get("props", {}).get("pageProps", {})
            product_details = page_props.get("productDetails")

            if not product_details:
                return None

            quantity = product_details.get("quantity")

            if quantity is None:
                return None

            return quantity

        except (KeyError, TypeError):
            return None

    except requests.exceptions.RequestException:
        return None


def save_to_csv(data):
    """
    将数据保存到CSV文件（追加模式），成功后触发 Git 推送
    修改：添加encoding='utf-8-sig'解决中文乱码问题
    """
    try:
        # 创建DataFrame
        df = pd.DataFrame([data])

        # 检查文件是否存在
        if os.path.exists(CSV_FILE):
            # 以追加模式写入，不包含列名，使用utf-8-sig编码解决中文乱码
            df.to_csv(CSV_FILE, mode='a', header=False, index=False, encoding='utf-8-sig')
        else:
            # 创建新文件，包含列名，使用utf-8-sig编码解决中文乱码
            df.to_csv(CSV_FILE, index=False, encoding='utf-8-sig')

        # 写入成功后触发 Git 推送
        git_push_update()

        return True
    except Exception as e:
        print(f"错误 - 无法写入CSV文件: {e}")
        return False


def monitor_stock_changes():
    """
    监控库存变化并记录销量
    """
    session = create_session()  # 创建带重试的会话

    while True:
        # 时间列已存储为文本格式并显示到秒单位
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        for url, address_name in product_urls.items():
            current_quantity = fetch_stock_data(url, session)

            if current_quantity is None:
                continue

            # 首次获取库存时初始化
            if url not in last_quantities:
                last_quantities[url] = current_quantity

                # 记录初始库存（只记录一次）
                if url not in initial_stock_printed:
                    initial_stock_printed[url] = True

                    # 准备数据
                    stock_change_str = f"初始销量: {current_quantity}"
                    single_sale = abs(current_quantity)

                    # 保存到CSV
                    data = {
                        "时间": timestamp,  # 时间列已经是文本格式
                        "商品名称": address_name,
                        "库存变化": stock_change_str,
                        "单笔销量": single_sale
                    }

                    if save_to_csv(data):
                        # 打印信息
                        print(f"{timestamp} - {address_name}: 初始库存: {current_quantity}")

            else:
                # 计算销量变化（库存减少表示销量增加）
                previous_quantity = last_quantities[url]
                sales_change = previous_quantity - current_quantity

                # 如果有销量变化
                if sales_change != 0:
                    # 准备数据
                    stock_change_str = f"{previous_quantity}->{current_quantity}"
                    single_sale = sales_change

                    # 保存到CSV
                    data = {
                        "时间": timestamp,  # 时间列已经是文本格式
                        "商品名称": address_name,
                        "库存变化": stock_change_str,
                        "单笔销量": single_sale
                    }

                    if save_to_csv(data):
                        # 打印信息
                        print(f"{timestamp} - {address_name}: 库存变化: {previous_quantity}->{current_quantity}, 销量变化: {sales_change}")

                # 更新当前库存
                last_quantities[url] = current_quantity

        # 每3秒检查一次库存
        time.sleep(10)


# 启动监控功能
if __name__ == "__main__":
    try:
        monitor_stock_changes()
    except KeyboardInterrupt:
        print("监控程序被用户终止")
    except Exception as e:
        print(f"监控程序发生未预期的错误: {e}")