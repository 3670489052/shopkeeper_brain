from logging import Logger
from typing import Tuple, List
import logging
import re

from openai import OpenAI

from knowledge.processor.import_processor.base import BaseNode
from knowledge.processor.import_processor.config import ImportConfig
from knowledge.processor.import_processor.exceptions import StateFieldError, FileProcessingError
from knowledge.processor.import_processor.state import ImportGraphState
from pathlib import Path

from knowledge.utils.clients.ai_clients import AIClients
import base64
from typing import Dict

from knowledge.utils.clients.storage_clients import StorageClients


class ImageContext:
    head_title:str
    pre_context:str
    post_context:str
    def __init__(self,head_title:str,pre_context:str,post_context:str):
        self.pre_context=pre_context
        self.post_context=post_context
        self.head_title=head_title
class ImageInfo:
    image_name:str
    image_path:str
    image_context:ImageContext
    def __init__(self,image_name:str,image_path:str,image_context:ImageContext):
        self.image_name=image_name
        self.image_path=image_path
        self.image_context=image_context

class MdFiledHandler:
    def __init__(self, logger: Logger, node_name: str):
        self.logger = logger
        self.node_name = node_name
    def read_md(self,md_path:str)->Tuple[str, Path, Path]:

        #1 判断md文件_path是否为空
        if not md_path:
            self.logger.error("md_path is empty")
            raise StateFieldError(node_name=self.node_name,field_name="md_path",expected_type=str)
        #2 判断md文件_path是否存在
        md_path_obj=Path(md_path)
        if not md_path_obj.exists():
            self.logger.error(f"md_path {md_path} not exists")
            raise FileProcessingError(node_name=self.node_name,message=f"md_path {md_path} not exists")
        #3 读取md文件内容到内存里
        with open(md_path_obj,"r",encoding="utf-8") as f:
            md_content=f.read()
        #4 获取md文件的图片和目录路径
        image_dir_obj=md_path_obj.parent/"images"
        # 5 返回md.content,image_dir_obj，md_path_obj
        return md_content,image_dir_obj,md_path_obj
    def back_up(self,md_content:str,md_path:str):
        # 1 备份md文件 应该在原文件的目录下 名字叫做原文件名字_new.原文件后缀
        md_path_obj=Path(md_path)
        backup_md_path_obj=md_path_obj.parent/(md_path_obj.stem+"_new"+md_path_obj.suffix)
        # 2 写入备份文件
        try:

            with open(backup_md_path_obj,"w",encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            self.logger.warning(f"文件备份失败 路径是{backup_md_path_obj}但是不影响程序运行")
        self.logger.info(f"文件备份成功 路径是{backup_md_path_obj}")

class ImageScanner():
    def __init__(self,config:ImportConfig):
        self.config = config

    def scan_img_dir(self,image_dir_obj:Path,md_content:str)->Tuple[List[ImageInfo],str]:
        image_info_list=[]
        # 2 遍历图片目录中每一个图片文件和上面的md行进行匹配
        for image_file in image_dir_obj.iterdir():
            # 2.1 过滤子目录
            if not image_file.is_file():
                continue
            # 2.2 过滤非图片文件类型
            if image_file.suffix not in self.config.image_extensions:
                continue
            # 2.3 在md_content中匹配到图片所在的行
            # 2.3.1 将md_content进行切分
            md_lines = md_content.split("\n")
            # 2.3.2 遍历md的每一行

            # 声明匹配md中图片的正则表达式
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file.name) + r".*?\)")
            # 默认没在代码块中
            in_code_fence = False
            # 定义匹配代码块的正则表达式
            code_fence_pattern = re.compile(r"^\s*'''")

            for index, md_line in enumerate(md_lines):

                # 3.3.3 判断其是否在代码块中
                if code_fence_pattern.match(md_line):
                    in_code_fence = not in_code_fence

                # 3.3.4 判断当前md_line 是否是这张图片
                if in_code_fence or not pattern.match(md_line):
                    continue

                # #如果匹配上了图片
                # print(f"匹配到图片 {image_file.name} 在第 {index} 行")
                # 3.3.5 到这里就表示已经匹配到图片 开始提取图片的上下文
                # 3.3.5.1 提取图片的上文
                # 定义匹配标题的正则表达式
                heading_pattern = re.compile(r"^#{1,6}\s+(.+)$")
                head_title,head_content=self._find_heading_content(md_lines,index,heading_pattern)
                # print(f"head_title:{head_title}")
                # print(f"head_content:{head_content}")
                # 3.3.5.2 提取图片的下文
                post_content=self._find_post_content(md_lines,index,heading_pattern)
                # print(md_line)
                # print(f"post_content:{post_content}")
                # 3.3.4 根据配置中的最大长度 对图片的上下文进行截取
                image_content_length=self.config.img_content_length
                # 3.3.4.1 截取图片的上文
                pre_context=self._extract_image_context(head_content,image_content_length,"pre")
                # 3.3.4.2 截取图片的下文
                post_context=self._extract_image_context(post_content,image_content_length,"down")
                # print(md_line)
                # print(f"pre_context:{pre_context}")
                # print(f"post_context:{post_context}")
                # 3.4 将图片的上下文封装到一个对象中
                image_context=ImageContext(head_title=head_title,pre_context=pre_context,post_context=post_context)
            #4 组装image_info对象
                image_info=ImageInfo(image_name=image_file.name,image_path=str(image_file),image_context=image_context)
            #5 将image_info对象添加到image_info_list中
                image_info_list.append(image_info)
        return image_info_list,md_lines
    def _find_heading_content(self,md_lines:List[str],image_index:int,heading_pattern)->Tuple[str, List[str]]:
        #定义找到最近标题的索引
        head_title_index=-1
        head_title=""
        #1 从当前图片的索引进行向上遍历 一直遍历到最前面 如果找到标题就停止循环  如果没找到就一直向前遍历
        in_code_fence=False
        # 定义匹配代码块的正则表达式
        code_fence_pattern = re.compile(r"^\s*'''")
        for i in range(image_index-1,-1,-1):
            line=md_lines[i]
            # 3.3.3 判断其是否在代码块中
            if code_fence_pattern.match(line):
                in_code_fence=not in_code_fence
            if heading_pattern.match(line) and not in_code_fence:
                head_title_index=i
                head_title=line
                break
        #2 获取文件上文
        head_content=md_lines[head_title_index+1:image_index]
        return head_title,head_content
    def _find_post_content(self,md_lines:List[str],image_index:int,heading_pattern:re.Pattern)->List[str]:
        in_code_fence = False
        # 定义匹配代码块的正则表达式
        code_fence_pattern = re.compile(r"^\s*'''")
        post_title_index = len(md_lines)
        #1 遍历图片索引向下遍历 找到离图片最近的标题为止  如果没找到就一直向下找
        for i in range(image_index+1,len(md_lines)):
            #匹配标题
            line = md_lines[i]
            # 3.3.3 判断其是否在代码块中
            if code_fence_pattern.match(line):
                in_code_fence = not in_code_fence
            if heading_pattern.match(line) and not in_code_fence:
                #找到了下标题
                post_title_index=i
                break
        #2 获取文件下文
        post_content=md_lines[image_index+1:post_title_index]
        return post_content
    # 截取上文内容
    def _extract_image_context(self, head_content:List[str], image_content_length:int,director:str)->str:
        # 定义匹配图片的正则表达式
        pattern = re.compile(r"!\[.*?\]\(.*?\)")
        # 定义存放当前内容的容器
        current_graph=[]
        #定义存放最终内容的容器
        final_graph=[]
        #1 遍历content
        for line in head_content:
            #2 判段如果line是空行 或者是其他图片 则表示这一段要结束了 要开始新的段落了
            if not line.strip() or pattern.match(line):
                #2.1 将当前行的内容添加到final_graph中 并且清空current_graph
                if current_graph:
                    final_graph.append("\n".join(current_graph))
                    current_graph=[]
            else :
                # 不需要开启新的段落 就将当前行的内容添加到current_graph中
                current_graph.append(line)
        # 遍历完成之后 如果current_graph还有内容 则添加到final_graph中
        if current_graph:
            final_graph.append("\n".join(current_graph))
        # 遍历final_graph 每个段落都截取前image_content_length个字符
        extract_graph=[]
        total_lenth=0
        # 如果是截取上文 那么应该将final_graph中的内容反转
        if director=="pre":
            final_graph.reverse()
        for graph in final_graph:
            if total_lenth>image_content_length:
                break
            extract_graph.append(graph)
            total_lenth+=len(graph)+len("\n\n")

        # 在截取完之后 拼接字符产之前 如果是截取上文 就将extract_graph中的内容反转
        if director=="pre":
            extract_graph.reverse()
        # 最终返回收集到的数据
        return "\n\n".join(extract_graph)

class VLMSummerizer():
    def __init__(self, logger: Logger,config:ImportConfig):
        self.logger = logger
        self.config=config
    def summarize_all(self,document_name:str,image_info_list:List[ImageInfo]):
        image_summaries={}

        #1 创建vlm对象
        try:
            vlm_clent=AIClients.get_vlm_client()
        except ConnectionError as e:
            self.logger.warning(f"创建vlm对象失败: {e}")
            # 需要给每张图片设置描述信息为"暂无摘要信息"
            for image_info in image_info_list:
                image_summaries[image_info.image_name]="暂无摘要信息"
            return image_summaries

        #2 遍历每一张照片 给每一张照片生成摘要信息
        for image_info in image_info_list:
            image_summaries[image_info.image_name]=self._summarize_single(document_name,image_info,vlm_clent)
        return image_summaries




    def _summarize_single(self,document_name:str,image_info:ImageInfo,vlm_clent:OpenAI):
        #1 获取文件上下文信息
        image_context=image_info.image_context
        #2 将上下文组装成字符串
        final_context=f"{image_context.head_title} \n {image_context.pre_context} \n {image_context.post_context}"
        #3 读取图片数据
        image_path=image_info.image_path
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # 2 组装给大模型输入的信息
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"任务：为Markdown文档中的图片生成一个简短的中文标题。\n"
                            f"背景信息：\n"
                            f"  1. 所属文档标题：\"{document_name}\"\n"
                            f"  2. 图片上下文：{final_context}\n"
                            f"请结合图片内容和上述上下文信息，"
                            f"用中文简要总结这张图片的内容，"
                            f"生成一个精准的中文标题摘要（不要包含图片二字）。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_data}"
                        },
                    },
                ]
            }
        ]

        # 3 调用大模型获取大模型的输出
        try:
            response=vlm_clent.chat.completions.create(
                model=self.config.vl_model,
                messages=messages,
        )
        except Exception as e:
            self.logger.warning(f"调用大模型失败: {e}")
            return "暂无摘要信息"
        # 4 解析大模型的输出 拿到每张图片的描述信息
        return response.choices[0].message.content.strip()

class ImageUploader():
    def __init__(self,logger: Logger,config:ImportConfig):
        self.logger = logger
        self.config=config
    def upload_and_replace(self,image_info_list:List[ImageInfo],image_summaries:Dict[str,str],document_name:str,md_lines:List[str]):
        #1 上传图片
        remote_urls=self.__upload_all(image_info_list,document_name)
        print(remote_urls)
        #2替换md中的内容 返回替换后的md内容
        new_md_content=self.__replace_md_content(md_lines,image_summaries,remote_urls)
        return new_md_content




    def __upload_all(self,image_info_list:List[ImageInfo],document_name:str)->Dict[str, str]:
        # 创建存放每张图片在minio服务器上的访问路径
        remote_urls = {}
        # 1 创建minio客户端对象
        try:
            minio_client = StorageClients.get_minio()
        except ConnectionError as e:
            self.logger.warning(f"创建minio客户端对象失败: {e}")
            # 所有图片兜底使用原路径
            for image_info in image_info_list:
                remote_urls[image_info.image_name] = image_info.image_path
            return remote_urls
        #2 调用minio客户端上传图片
        for image_info in image_info_list:
            #构建objectname  就是文件存储在minio上的路径  文档名字/图片名字  万用表rs-12的使用/1.jpg
            object_name=document_name+"/"+image_info.image_name
            bucket_name=self.config.minio_bucket
            try:
                minio_client.fput_object(
                bucket_name=self.config.minio_bucket,
                object_name=object_name,
                file_path=image_info.image_path,
            )
            except Exception as e:
                #兜底使用图片的原路径
                remote_urls[image_info.image_name]=image_info.image_path
                continue
            #组装文件在minio上访问的路径
            remote_url=self.config.get_minio_base_url()+"/"+bucket_name+"/"+object_name
            remote_urls[image_info.image_name]=remote_url
        return remote_urls
    def __replace_md_content(self,md_lines:List[str],image_summaries:Dict[str,str],remote_urls:Dict[str, str])->str:
        #遍历md_lines

        # 定义正则表达式匹配md中的图片，定义两个匹配组出来，第一个匹配组用于匹配摘要，第二个匹配组用于匹配路径
        pattern = re.compile(r"!\[(.*?)\]\((.*?)\)")
        in_code_fence=False
        code_fence_pattern = re.compile(r"^\s*'''")

        #声明一个容器 存储替换后的md内容
        new_md_lines=[]
        for line in md_lines:
            if code_fence_pattern.match(line):
                in_code_fence=not in_code_fence
            # 如果这行是图片 并且不在代码块里就替换
            match=pattern.match(line)
            if not in_code_fence and match:
                #获取图片名（match[2]是图片路径，需取最后一段文件名）
                image_name=Path(match[2]).name
                new_md_lines.append(f"![{image_summaries[image_name]}]({remote_urls[image_name]})")
            else:
                #不需要替换
                new_md_lines.append(line)
        return "\n".join(new_md_lines)




class MdImageNode(BaseNode):
    name="md_image_node"
    def __init__(self,logger: Logger,node_name: str):
        super().__init__()
        self.logger = logger
        self.node_name = node_name
        self.md_file_handler = MdFiledHandler(logger=self.logger, node_name=self.node_name)
        self.image_scanner = ImageScanner(self.config)
        self.vl_summerizer=VLMSummerizer(self.logger,self.config)
        self.image_uploader=ImageUploader(self.logger,self.config)
    def process(self,state:ImportGraphState):
        #1 读取md文件内容到内存里
        md_path=state.get("md_path")
        md_content,image_dir_obj,md_path_obj=self.md_file_handler.read_md(md_path)
        # print(md_content)
        # print(image_dir_obj)
        # print(md_path_obj)
        # 2 遍历图片目录中每一个图片文件和上面的md行进行匹配
        image_info_list,md_lines=self.image_scanner.scan_img_dir(image_dir_obj,md_content)
        # print(image_info_list)






        #4 根据图片上下文 使用vlm模型 识别图片 获取图片描述信息
        image_summaries=self.vl_summerizer.summarize_all(document_name=md_path_obj.stem,image_info_list=image_info_list)
        # print(image_summaries)
        #5 将图片上传到minio服务器  #6 将图片的描述信息 minio中的url填回md文件中
        new_md_content=self.image_uploader.upload_and_replace(
            image_info_list=image_info_list,
            image_summaries=image_summaries,
            document_name=md_path_obj.stem,
            md_lines=md_lines,
        )
        print(new_md_content)

        #7 将回填后的内容 备份成新的md文件 便于测试观察
        self.md_file_handler.back_up(new_md_content,md_path)
        state["md_content"]=new_md_content
        return state
if __name__ == "__main__":
    state={
        "md_path":r"D:\python\shopkeeper-brain\knowledge\processor\import_processor\output_dir\万用表RS-12的使用\万用表RS-12的使用.md"

    }
    node = MdImageNode(logger=logging.getLogger("test"), node_name="md_image_node")
    #node=MdImageNode()
    node.process(state)
