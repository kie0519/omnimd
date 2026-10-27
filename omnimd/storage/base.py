"""存储上传契约

upload_image(image_data, filename) -> 公开 URL——VLM 端点据此拉取图片。
必填变量在 REQUIRED_ENV_VARS 声明，构造时按声明校验（缺一报全清单）。
"""
from abc import ABC, abstractmethod


class StorageUploader(ABC):
    """上传图片二进制，返回一个可长期公开访问的 URL。

    子类在 REQUIRED_ENV_VARS 中声明必填的环境变量名（缺一报全清单）。
    """

    #: 本后端必填的环境变量名元组
    REQUIRED_ENV_VARS: tuple[str, ...] = ()

    @abstractmethod
    def upload_image(self, image_data: bytes, filename: str = "image.png") -> str:
        """上传单张图片，返回公开 URL。失败抛异常。"""
        raise NotImplementedError
