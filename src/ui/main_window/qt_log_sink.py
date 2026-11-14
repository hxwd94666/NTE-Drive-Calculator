# 将标准日志文本转发到主窗口的 Qt 信号。
"""只将非空日志行交给 Qt 信号；日志生成与落盘仍归日志 owner。"""


class QtLogSink:
    def __init__(self, signal):
        self.signal = signal

    def write(self, message):
        text = message.strip()
        if text:
            self.signal.emit(text)

    def flush(self):
        pass
