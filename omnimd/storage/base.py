"""存储后端抽象契约

STORAGE_PROVIDER 的合法取值与「每个后端需要什么配置」都定义在这里：
各后端在 REQUIRED_ENV_VARS 中声明自己的必填变量，工厂按声明校验。
新增后端 = 子类 + 声明 + 工厂注册一行，配置校验不用改。
"""
from abc import ABC, abstractmethod


class StorageUploader(ABC):
    """上传图片二进制，返回一个可长期公开访问的 URL。

    子类约定：
    - 声明 REQUIRED_ENV_VARS：本后端必填的环境变量名（缺一时启动报错）
    - 只读自己在 Config 上对应的字段，未选中后端的字段不校验、不读取
    """

    #: 本后端必填的环境变量名元组
    REQUIRED_ENV_VARS: tuple[str, ...] = ()

    @abstractmethod
    def upload_image(self, image_data: bytes, filename: str = "image.png") -> str:
        """上传单张图片，返回公开 URL。失败抛异常。"""
        raise NotImplementedError
