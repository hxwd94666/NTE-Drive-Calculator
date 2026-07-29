# 将标准日志文本转发到主窗口的 Qt 信号。
"""Small file-like adapter used by the in-window log view."""


class QtLogSink:
    def __init__(self, signal):
        self.signal = signal

    def write(self, message):
        text = message.strip()
        if text:
            self.signal.emit(text)

    def flush(self):
        pass
