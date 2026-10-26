import shutil
import time
import zipfile
from pathlib import Path

from knowledge.processor.import_processor.base import BaseNode, setup_logging
from knowledge.processor.import_processor.exceptions import ValidationError
from knowledge.processor.import_processor.state import ImportGraphState
import requests


class pdf_to_md_node(BaseNode):
    def process(self,state:ImportGraphState):
        self.log_step("开始处理")
        #1 上传并轮询mineru的解析结果
        pdf_path=state.get("pdf_path")
        pdf_path_obj=Path(pdf_path)
        zip_url=self._upload_and_query_result(pdf_path_obj)
        print(zip_url)

        #2 下载zip包并且提取md文件
        self.log_step("第二步  开始下载zip包")

        file_dir=state.get("file_dir")
        file_dir_obj=Path(file_dir)

        md_path=self._extract_zip(zip_url,file_dir_obj,pdf_path_obj)
        #3 将md_path添加到state中
        state["md_path"]=md_path

    def _upload_and_query_result(self,pdf_path:str)->str:
        print("token:", repr(self.config.mineru_api_token))
        print("url:", repr(self.config.mineru_base_url))

        #1检查mineru的配置
        mineru_api_token=self.config.mineru_api_token
        mineru_base_url=self.config.mineru_base_url
        if not mineru_api_token or not mineru_base_url:
            self.logger.error("mineru_api_token or mineru_base_url not set")
            raise ValidationError(node_name=self.name,message="mineru_api_token or mineru_base_url not set")
        #2 获取文件上传结果
        #2.1 获取url
        url=mineru_base_url+"/file-urls/batch"
        #2.2 构建headers
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {mineru_api_token}"
        }
        #2.3 构建请求体date
        data = {
            "files": [
                {"name": f"{pdf_path.name}", "data_id": "abcd"}
            ],
            "model_version": "vlm"
        }
        #2.4 发送请求 获取上传链接
        try:
            response = requests.post(url=url,headers=header,json=data)
        # response = requests.post(url, headers=header, json=data)
        except Exception as e:
            self.logger.error(f"获取上传链接失败 异常信息为 {e}")
            raise RuntimeError(f"获取上传链接失败 异常信息为 {e}")
        #2.5 判断请求是否成功  包含http的响应状态码是不是200
        if not response.status_code == 200:
            #说明http请求失败 流程终止
            self.logger.error(f"接口调用失败 状态码为{response.status_code} 响应内容为{response}")
            raise RuntimeError(f"接口调用失败 状态码为{response.status_code} 响应内容为{response}")

        #2，6 获取响应结果
        result=response.json()
        # 2.7 判断业务状态码
        if result["code"]!=0:
            #说明获取上传链接没有成功
            self.logger.error(f"获取上传链接失败 业务状态码为{result['code']} 响应内容为{result}")
            raise RuntimeError(f"获取上传链接失败 业务状态码为{result['code']} 响应内容为{result}")
        #2.8 提取上传链接和batch_id
        batch_id=result["data"]["batch_id"]
        upload_url=result["data"]["file_urls"][0]
        self.logger.info(f"获取上传链接成功 batch_id={batch_id} upload_url={upload_url}")


        #3 上传pdf文件到mineru
        #3.1 发送请求发送pdf
        try:
            with open(str(pdf_path),"rb") as f:
                file_content=f.read()
                res_upload = requests.put(upload_url, data=file_content)
        except Exception as e:
            self.logger.error(f"上传pdf失败 异常信息为 {e}")
            raise RuntimeError(f"上传pdf失败 异常信息为 {e}")

        #3.2 判断状态码表示是否上传成功
        if res_upload.status_code != 200:
            self.logger.error(f"上传pdf失败 状态码为{res_upload.status_code} 响应内容为{res_upload}")
            raise RuntimeError(f"上传pdf失败 状态码为{res_upload.status_code} 响应内容为{res_upload}")
        self.logger.info(f"上传pdf成功 batch_id={batch_id} upload_url={upload_url}")

        #4 轮询mineru解析结果 知道成功 失败 或者超时
        #定义最大超时时间
        max_time=30
        #定义轮询间隔
        interval_time=3
        #定义开始时间
        start_time=time.time()
        while True:
            end_time=time.time()
            if end_time-start_time>max_time:
                #报时超过最大超时时间 终止轮询
                self.logger.warning(f"轮询解析失败 超时时间超过{max_time}秒")
                break
            pull_url=mineru_base_url+f"/extract-results/batch/{batch_id}"
            pull_res=requests.get(pull_url,headers=header)
            #判断响应状态码 如果不是200 表示查询失败
            if pull_res.status_code!=200:
                self.logger.warning(f"轮询解析失败 解析状态码为{pull_res.status_code}")
                #等待轮询间隔
                time.sleep(interval_time)
                continue
            #判断业务状态码
            pull_result=pull_res.json()
            if pull_result["code"]!=0:
                self.logger.warning(f"轮询解析失败 业务状态码为{pull_result['code']} ")
                #等待轮询间隔
                time.sleep(interval_time)
                continue
            #获取解析结果
            extract_result=pull_result["data"]["extract_result"]
            #获取解析状态
            extract_res=extract_result[0]["state"]
            if extract_res=="done":
                #表示mineru真正完成了pdf转md的过程
                full_zip_url=extract_result[0]["full_zip_url"]
                self.logger.info(f"mineru解析成功 batch_id={batch_id} full_zip_url={full_zip_url}")
                return full_zip_url
            elif extract_res=="failed":
                # 表示mineru解析pdf转md的过程失败
                self.logger.error(f"mineru解析失败 batch_id={batch_id}")
                raise RuntimeError(f"mineru解析失败 batch_id={batch_id}")
            else:
                #说明还没解析成功  需要继续轮询
                #等待轮询间隔
                time.sleep(interval_time)
                continue





    def _extract_zip(self, zip_url:str,file_dir_obj:Path,pdf_path_obj:Path)->str:
        #1下载zip包  发送get请求下载zip包
        try:
            response=requests.get(zip_url,timeout=30)
        except Exception as e:
            self.logger.error(f"下载zip包失败 异常信息为 {e}")
            raise RuntimeError(f"下载zip包失败 异常信息为 {e}")
        if response.status_code!=200:
            self.logger.error(f"下载zip包失败 状态码为{response.status_code} 响应内容为{response}")
            raise RuntimeError(f"下载zip包失败 状态码为{response.status_code} 响应内容为{response}")
        self.logger.info(f"下载zip包成功 zip_url={zip_url},文件名={pdf_path_obj.stem}")
        #2 指定zip包的存储路径 将下载的zip包写到该路径下
        #2.1 构建zip包的存储路径 就是放在file_dir/文件名_result.zip
        zip_path=file_dir_obj/f"{pdf_path_obj.stem}_result.zip"

        #2.2 将response的内容写入zip包路径下
        try:
            with open(zip_path,"wb") as f:
                f.write(response.content)
        except Exception as e:
            self.logger.error(f"写入zip包失败 异常信息为 {e}")
            raise RuntimeError(f"写入zip包失败 异常信息为 {e}")
        self.logger.info(f"写入zip包成功 zip_path={zip_path}")

        #3 解压zip包
        #3.1 构建zip包的解压路径 filr_dir/文件名 例如 file_dir/万用表RS-12的使用
        extract_path=file_dir_obj/f"{pdf_path_obj.stem}"
        #3.2 如果该目录已经有内容了 要先清除目录下所有内容
        try:
            if extract_path.exists():
                shutil.rmtree(extract_path)

        except Exception as e:
            self.logger.warning(f"清除目录下所有内容失败 异常信息为 {e},目标目录={extract_path},但不影响后续解压")

        #3.3 解压zip包到解压路径下
        try:
            with zipfile.ZipFile(zip_path,"r") as zip_ref:
                zip_ref.extractall(extract_path)
        except Exception as e:
            self.logger.error(f"解压zip包失败 异常信息为 {e}")
            raise RuntimeError(f"解压zip包失败 异常信息为 {e}")
        self.logger.info(f"解压zip包成功 zip_path={zip_path},解压路径={extract_path}")
        #3.4 将md文件进行重命名 pdf文件名.md
        #3.4.1 获取要重命名的文件路径
        md_file_path = next(extract_path.rglob("*.md"))
        #3.4.2 构建新的文件名 extract_path/源文件名字.md
        new_md_file_path=extract_path/f"{pdf_path_obj.stem}.md"
        #3.4.3 重命名文件
        md_file_path.rename(new_md_file_path)

        #4返回新的命名路径名
        return new_md_file_path


if __name__ == "__main__":
    setup_logging()
    pdf=pdf_to_md_node()
    state={
        "pdf_path": r"D:\python\shopkeeper-brain\knowledge\processor\input_dir\万用表RS-12的使用.pdf",
        "file_dir": r"D:\python\shopkeeper-brain\knowledge\processor\import_processor\output_dir"
    }
    pdf(state)