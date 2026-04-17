import psutil
import tkinter as tk
from tkinter import ttk
import time
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib

# 尝试导入 GPU 库，如果没有安装则设为 None
try:
    from pynvml import *

    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False
    print("提示：未检测到 pynvml 库，GPU 监控功能将不可用。如需启用，请运行: pip install pynvml")

# --- 基础配置 ---
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False


class SystemMonitorApp:
    def __init__(self, root):
        global GPU_AVAILABLE
        self.root = root
        self.root.title("🐍 全维度系统监控 (CPU/GPU/Net/Disk)")
        self.root.geometry("1300x950")

        # --- 调整布局权重：大幅增加表格区域占比 ---
        self.root.grid_rowconfigure(1, weight=2)  # 图表区域 (权重较小，占据更少空间)
        self.root.grid_rowconfigure(2, weight=3)  # 表格区域 (权重较大，占据更多空间)
        self.root.grid_columnconfigure(0, weight=1)

        # --- 数据结构 (双端队列用于存储历史数据) ---
        self.cpu_history = deque([0] * 50, maxlen=50)
        self.mem_history = deque([0] * 50, maxlen=50)
        self.disk_history = deque([0] * 50, maxlen=50)
        self.gpu_history = deque([0] * 50, maxlen=50)

        # 网络数据 (存储速度，而非总量)
        self.net_upload_history = deque([0] * 50, maxlen=50)
        self.net_download_history = deque([0] * 50, maxlen=50)
        self.last_net_io = psutil.net_io_counters(pernic=True)
        self.last_net_time = time.time()

        # 排序状态
        self.sort_column = None
        self.reverse_sort = False

        # --- 界面布局 ---
        self.create_widgets()

        # --- 初始化 GPU ---
        if GPU_AVAILABLE:
            try:
                nvmlInit()
                self.gpu_handle = nvmlDeviceGetHandleByIndex(0)
            except Exception:
                GPU_AVAILABLE = False

        # --- 启动后台线程 ---
        self.running = True
        self.thread = threading.Thread(target=self.update_data_loop, daemon=True)
        self.thread.start()

    def create_widgets(self):
        # === 1. 顶部标题和数值 ===
        header_frame = tk.Frame(self.root, pady=10)
        header_frame.grid(row=0, column=0, sticky="ew")
        title_label = tk.Label(header_frame, text="全维度资源实时监控", font=("Microsoft YaHei", 20, "bold"))
        title_label.pack(side="left", padx=20)

        # 简单的数值汇总
        self.summary_label = tk.Label(header_frame, text="准备中...", font=("Microsoft YaHei", 14), fg="gray")
        self.summary_label.pack(side="right", padx=20)

        # === 2. 图表区域 (3行2列布局) ===
        chart_frame = tk.LabelFrame(self.root, text="实时趋势图", font=("Microsoft YaHei", 16), padx=10, pady=10)
        chart_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        chart_frame.grid_propagate(False)  # --- 关键修改：禁止自动调整大小 ---

        chart_frame.grid_columnconfigure(0, weight=1)
        chart_frame.grid_columnconfigure(1, weight=1)
        chart_frame.grid_rowconfigure(0, weight=1)
        chart_frame.grid_rowconfigure(1, weight=1)
        chart_frame.grid_rowconfigure(2, weight=1)

        # 创建 Matplotlib 图形 (3行2列)
        self.fig, self.axs = plt.subplots(3, 2, figsize=(14, 12))
        self.fig.tight_layout(pad=4.0)

        # 设置标题
        self.axs[0, 0].set_title("处理器 & 显卡利用率 (%)")
        self.axs[0, 1].set_title("内存 & 磁盘利用率 (%)")
        self.axs[1, 0].set_title("网络实时速度 (KB/s)")

        # 隐藏右下角的空轴
        self.axs[1, 1].axis('off')
        self.axs[2, 0].axis('off')
        self.axs[2, 1].axis('off')

        # 初始化线条对象
        # 行1: CPU + GPU
        self.line_cpu, = self.axs[0, 0].plot(self.cpu_history, label="CPU", color='blue')
        if GPU_AVAILABLE:
            self.line_gpu, = self.axs[0, 0].plot(self.gpu_history, label="GPU", color='orange')
        self.axs[0, 0].set_ylim(0, 100)
        self.axs[0, 0].legend(loc="upper right")
        self.axs[0, 0].grid(True, linestyle='--', alpha=0.5)

        # 行2: 内存 + 磁盘
        self.line_mem, = self.axs[0, 1].plot(self.mem_history, label="内存", color='green')
        self.line_disk, = self.axs[0, 1].plot(self.disk_history, label="磁盘", color='red')
        self.axs[0, 1].set_ylim(0, 100)
        self.axs[0, 1].legend(loc="upper right")
        self.axs[0, 1].grid(True, linestyle='--', alpha=0.5)

        # 行3: 网络
        self.line_up, = self.axs[1, 0].plot(self.net_upload_history, label="上传速度", color='cyan')
        self.line_down, = self.axs[1, 0].plot(self.net_download_history, label="下载速度", color='magenta')
        self.axs[1, 0].set_ylim(0, 1000)  # 初始Y轴范围，会自动调整
        self.axs[1, 0].legend(loc="upper right")
        self.axs[1, 0].grid(True, linestyle='--', alpha=0.5)

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas.get_tk_widget().grid(row=0, column=0, columnspan=2, sticky="nsew")

        # === 3. 进程列表区域 ===
        list_frame = tk.LabelFrame(self.root, text="所有进程资源占用 (点击表头排序)", font=("Microsoft YaHei", 16))
        list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        list_frame.grid_propagate(False)  # --- 关键修改：禁止自动调整大小 ---

        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Treeview", font=("Microsoft YaHei", 12), rowheight=25)
        style.configure("Treeview.Heading", font=("Microsoft YaHei", 12, "bold"))

        columns = ("pid", "name", "cpu", "memory", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("pid", text="PID", command=lambda: self.sort_treeview("pid", False))
        self.tree.column("pid", width=80, anchor="center")
        self.tree.heading("name", text="进程名称", command=lambda: self.sort_treeview("name", False))
        self.tree.column("name", width=300, anchor="w")
        self.tree.heading("cpu", text="CPU %", command=lambda: self.sort_treeview("cpu", True))
        self.tree.column("cpu", width=100, anchor="center")
        self.tree.heading("memory", text="内存 (MB)", command=lambda: self.sort_treeview("memory", True))
        self.tree.column("memory", width=120, anchor="center")
        self.tree.heading("status", text="状态", command=lambda: self.sort_treeview("status", False))
        self.tree.column("status", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def get_network_speed(self):
        try:
            current_net = psutil.net_io_counters(pernic=True)
            current_time = time.time()
            time_diff = current_time - self.last_net_time

            # 尝试获取以太网或Wi-Fi，如果没有则取所有接口的总和
            upload_speed = 0
            download_speed = 0

            # 简单的逻辑：优先找以太网或Wi-Fi，找不到就累加所有
            target_nics = ['以太网', 'Ethernet', 'Wi-Fi', 'WLAN']
            found = False
            for nic_name in target_nics:
                if nic_name in current_net:
                    last = self.last_net_io[nic_name]
                    curr = current_net[nic_name]
                    upload_speed = (curr.bytes_sent - last.bytes_sent) / time_diff / 1024  # KB/s
                    download_speed = (curr.bytes_recv - last.bytes_recv) / time_diff / 1024
                    found = True
                    break

            if not found:
                # fallback: 计算所有接口的总速度
                total_sent = sum(nic.bytes_sent for nic in current_net.values())
                total_recv = sum(nic.bytes_recv for nic in current_net.values())
                last_total_sent = sum(nic.bytes_sent for nic in self.last_net_io.values())
                last_total_recv = sum(nic.bytes_recv for nic in self.last_net_io.values())
                upload_speed = (total_sent - last_total_sent) / time_diff / 1024
                download_speed = (total_recv - last_total_recv) / time_diff / 1024

            self.last_net_io = current_net
            self.last_net_time = current_time
            return max(0, upload_speed), max(0, download_speed)
        except Exception:
            return 0, 0

    def update_data_loop(self):
        while self.running:
            try:
                # 1. CPU & Memory & Disk
                cpu_percent = psutil.cpu_percent(interval=0.5)
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('/')

                # 2. GPU (如果可用)
                gpu_percent = 0
                if GPU_AVAILABLE:
                    try:
                        handle = nvmlDeviceGetHandleByIndex(0)
                        info = nvmlDeviceGetUtilizationRates(handle)
                        gpu_percent = info.gpu
                    except:
                        gpu_percent = 0

                # 3. Network
                up_speed, down_speed = self.get_network_speed()

                # 4. 更新历史数据
                self.cpu_history.append(cpu_percent)
                self.mem_history.append(mem.percent)
                self.disk_history.append(disk.percent)
                self.gpu_history.append(gpu_percent)
                self.net_upload_history.append(up_speed)
                self.net_download_history.append(down_speed)

                # 5. 获取进程数据
                process_list = []
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']):
                    try:
                        if proc.info['name'] == "System Idle Process":
                            continue
                        mem_mb = proc.info['memory_info'].rss / 1024 / 1024
                        # 注意：第一次调用cpu_percent可能返回0.0，这是正常的
                        process_list.append({
                            "pid": proc.info['pid'],
                            "name": proc.info['name'],
                            "cpu": proc.info['cpu_percent'],
                            "memory": mem_mb,
                            "status": proc.info['status']
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                # 排序逻辑：默认按CPU降序
                if not self.sort_column:
                    process_list.sort(key=lambda x: x['cpu'], reverse=True)

                # 6. 更新 UI
                self.root.after(0, self.update_ui, cpu_percent, mem.percent, disk.percent, gpu_percent, up_speed,
                                down_speed, process_list)

            except Exception as e:
                print(e)
                time.sleep(1)

    def update_ui(self, cpu, mem, disk, gpu, up_sp, down_sp, processes):
        # 更新顶部文字
        self.summary_label.config(
            text=f"CPU: {cpu:.1f}% | GPU: {gpu:.1f}% | 内存: {mem:.1f}% | 磁盘: {disk:.1f}% | ↓{down_sp:.1f} KB/s ↑{up_sp:.1f} KB/s")

        # 更新图表数据
        self.line_cpu.set_ydata(self.cpu_history)
        self.line_cpu.set_xdata(range(len(self.cpu_history)))

        if GPU_AVAILABLE:
            self.line_gpu.set_ydata(self.gpu_history)
            self.line_gpu.set_xdata(range(len(self.gpu_history)))

        self.line_mem.set_ydata(self.mem_history)
        self.line_mem.set_xdata(range(len(self.mem_history)))

        self.line_disk.set_ydata(self.disk_history)
        self.line_disk.set_xdata(range(len(self.disk_history)))

        self.line_up.set_ydata(self.net_upload_history)
        self.line_up.set_xdata(range(len(self.net_upload_history)))

        self.line_down.set_ydata(self.net_download_history)
        self.line_down.set_xdata(range(len(self.net_download_history)))

        # 自动调整网络图表的Y轴范围
        max_net = max(max(self.net_upload_history), max(self.net_download_history), 10)
        self.axs[1, 0].set_ylim(0, max_net * 1.1)

        # 重绘图表
        for ax in self.axs.flat:
            if not (ax is self.axs[1, 1] or ax is self.axs[2, 0] or ax is self.axs[2, 1]):  # 跳过隐藏的
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
        self.canvas.draw()

        # 更新表格 (保持当前排序状态)
        self.update_treeview_data(processes)

    def update_treeview_data(self, processes):
        # 如果有自定义排序，则应用它，否则保持传入的顺序（默认已按CPU排序）
        if self.sort_column:
            # 这里的逻辑在 sort_treeview 中处理，这里只负责刷新数据
            # 为了不闪烁，最好保留当前排序逻辑
            # 简单起见，如果正在排序，我们在这里重新执行一次排序逻辑
            # 注意：processes 是未排序的原始列表（除非我们在上面排序了）
            # 为了简化，我们在上面 update_data_loop 中已经处理了默认排序
            pass

        # 刷新列表
        for i in self.tree.get_children():
            self.tree.delete(i)

        # 只插入前 150 个，防止卡顿
        for proc in processes[:150]:
            self.tree.insert("", "end", values=(
                proc['pid'],
                proc['name'],
                f"{proc['cpu']:.1f}",
                f"{proc['memory']:.1f}",
                proc['status']
            ))

    def sort_treeview(self, col, is_numeric):
        # 获取当前数据
        items = self.tree.get_children()
        values = []
        for item in items:
            val = self.tree.item(item, "values")
            # 转换数值类型以便正确排序
            if col == "cpu":
                sort_val = float(val[2])
            elif col == "memory":
                sort_val = float(val[3])
            elif col == "pid":
                sort_val = int(val[0])
            else:
                sort_val = val[1]  # name 或 status
            values.append((sort_val, val))

        # 切换升序/降序
        if self.sort_column == col:
            self.reverse_sort = not self.reverse_sort
        else:
            self.sort_column = col
            self.reverse_sort = False

        # 排序
        values.sort(reverse=self.reverse_sort)

        # 重新插入
        self.tree.delete(*self.tree.get_children())
        for _, val in values:
            self.tree.insert("", "end", values=val)


if __name__ == "__main__":
    root = tk.Tk()
    app = SystemMonitorApp(root)
    root.mainloop()
