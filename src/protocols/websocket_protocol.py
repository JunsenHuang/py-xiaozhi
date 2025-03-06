import asyncio
import json
import logging
import websockets
import  base64

from src.protocols.protocol import Protocol
from src.utils.config_manager import ConfigManager

# 获取配置管理器实例
config = ConfigManager.get_instance()

logger = logging.getLogger("WebsocketProtocol")


class WebsocketProtocol(Protocol):
    def __init__(self):
        super().__init__()
        self.websocket = None
        self.server_sample_rate = 16000
        self.connected = False
        self.hello_received = asyncio.Event()
        self.config = ConfigManager.get_instance()
        self.WEBSOCKET_URL = config.get_config("NETWORK.WEBSOCKET_URL")
        self.CLIENT_ID = config.get_client_id()
        self.DEVICE_ID = config.get_device_id()

    async def connect(self) -> bool:
        """连接到WebSocket服务器"""
        try:
            # 配置连接
            headers = {
                "Authorization": f"Bearer test-token",
                "Protocol-Version": "1",
                "Device-Id": self.DEVICE_ID,  # 获取设备MAC地址
                "Client-Id": self.CLIENT_ID
            }

            # 建立WebSocket连接
            self.websocket = await websockets.connect(self.WEBSOCKET_URL, additional_headers=headers, proxy=None)

            # 启动消息处理循环
            asyncio.create_task(self._message_handler())

            # 发送客户端hello消息
            hello_message = {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60
                }
            }
           # await self.send_text(json.dumps(vl_message))
            await self.send_text(json.dumps(hello_message))

            # 等待服务器hello响应
            try:
                await asyncio.wait_for(self.hello_received.wait(), timeout=10.0)
                self.connected = True
                logger.info("已连接到WebSocket服务器")
                return True
            except asyncio.TimeoutError:
                logger.error("等待服务器hello响应超时")
                if self.on_network_error:
                    self.on_network_error("等待响应超时")
                return False

        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")
            if self.on_network_error:
                self.on_network_error(f"无法连接服务: {str(e)}")
            return False

    async def _message_handler(self):
        """处理接收到的WebSocket消息"""
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")
                        if msg_type == "hello":
                            await self._handle_server_hello(data)
                        else:
                            if self.on_incoming_json:
                                self.on_incoming_json(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"无效的JSON消息: {message}, 错误: {e}")
                else:
                    # 处理二进制音频数据
                    if self.on_incoming_audio:
                        self.on_incoming_audio(message)

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket连接已关闭")
            self.connected = False
            if self.on_audio_channel_closed:
                await self.on_audio_channel_closed()
        except Exception as e:
            logger.error(f"消息处理错误: {e}")
            self.connected = False
            if self.on_network_error:
                self.on_network_error(f"连接错误: {str(e)}")

    # base64图片压缩
    def compress_image(self, image_path):
        with open(image_path, 'rb') as image_:
            return base64.b64encode(image_.read()).decode('utf-8')

    async def send_audio(self, data: bytes):
        """发送音频数据"""
        if self.websocket and self.connected:
            try:
                await self.websocket.send(data)
            except Exception as e:
                logger.error(f"发送音频数据失败: {e}")
                if self.on_network_error:
                    self.on_network_error(f"发送音频失败: {str(e)}")

    async def send_image(self, capture_frame_to_base64_to_json: str):
        """发送文本消息"""
        if self.websocket:
            try:
                await self.websocket.send(capture_frame_to_base64_to_json)
            except Exception as e:
                logger.error(f"发送文本消息失败: {e}")
                if self.on_network_error:
                    self.on_network_error(f"发送消息失败: {str(e)}")

    async def send_text(self, message: str):
        """发送文本消息"""
        if self.websocket:
            try:
                await self.websocket.send(message)
            except Exception as e:
                logger.error(f"发送文本消息失败: {e}")
                if self.on_network_error:
                    self.on_network_error(f"发送消息失败: {str(e)}")

    def is_audio_channel_opened(self) -> bool:
        """检查音频通道是否打开"""
        return self.websocket is not None and self.connected

    async def open_audio_channel(self) -> bool:
        """打开音频通道"""
        if not self.connected:
            return await self.connect()
        return True

    async def _handle_server_hello(self, data: dict):
        """
        处理服务器的 hello 消息
        参考 C++ 版本的 ParseServerHello 函数实现
        """
        try:
            # 验证传输方式
            transport = data.get("transport")
            if not transport or transport != "websocket":
                logger.error(f"不支持的传输方式: {transport}")
                return

            # 获取音频参数
            audio_params = data.get("audio_params")
            if audio_params:
                # 获取服务器的采样率
                sample_rate = audio_params.get("sample_rate")
                if sample_rate:
                    self.server_sample_rate = sample_rate
                    # 如果服务器采样率与本地不同，记录警告
                    if sample_rate != self.server_sample_rate:
                        logger.warning(
                            f"服务器的音频采样率 {sample_rate} "
                            f"与设备输出的采样率 {self.server_sample_rate} 不一致，"
                            "重采样后可能会失真"
                        )

            # 设置 hello 接收事件
            self.hello_received.set()

            # 通知音频通道已打开
            if self.on_audio_channel_opened:
                await self.on_audio_channel_opened()

            logger.info("成功处理服务器 hello 消息")

        except Exception as e:
            logger.error(f"处理服务器 hello 消息时出错: {e}")
            if self.on_network_error:
                self.on_network_error(f"处理服务器响应失败: {str(e)}")

    async def close_audio_channel(self):
        """关闭音频通道"""
        if self.websocket:
            try:
                await self.websocket.close()
                self.websocket = None
                self.connected = False
                if self.on_audio_channel_closed:
                    await self.on_audio_channel_closed()
            except Exception as e:
                logger.error(f"关闭WebSocket连接失败: {e}")