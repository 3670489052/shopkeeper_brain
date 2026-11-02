from pathlib import Path

from knowledge.processor.import_processor.base import BaseNode
from knowledge.processor.import_processor.config import ImportConfig
from knowledge.processor.import_processor.state import ImportGraphState
from knowledge.processor.import_processor.exceptions import StateFieldError
from knowledge.processor.import_processor.exceptions import ValidationError
from typing import Any, List, Tuple,Dict
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from resource.markdown_util import MarkdownTableLinearizer
class DocumentSplitNode(BaseNode):
    #self.name="document_split_node"
    
    def process(self,state:ImportGraphState)->ImportGraphState:
        self.name="document_split_node"
        #1 参数校验
        new_md_content, file_title, max_chunk_length, min_chunk_length = self.validate_state(state,self.config)
        
        #2 按标题切分
        sections_by_head=self.split_by_head(new_md_content,file_title)
        # for section in sections_by_head:
        #     print(section)
        #3 二次切分或者合并
        final_sections=self.split_and_merge(sections_by_head,max_chunk_length,min_chunk_length)
        
        #4 将切分完的内容 组装成后续节点可以使用的chunks
        chunks=self.assemble_chunks(final_sections)
        
        #5 将chunks转化成json格式 以便后续测试
        self.backup_chunks(chunks,state)
        state["chunks"]=chunks
        return state
    
    def validate_state(self,state:ImportGraphState,config:ImportConfig)->Tuple[str,str,int,int]:
        #1 统一换行符 将\r\n 替换成 \n
        #1.1 获取state中的md_content
        md_content=state.get("md_content","")
        mid_md_content=md_content.replace("\r","\n")
        new_md_content=mid_md_content.replace("\r\n","\n\n")

        #2 校验配置信息 一个切片最大长度 一个最小长度
        max_content_length=config.max_content_length
        min_content_length=config.min_content_length
        if max_content_length <= 0 or min_content_length <= 0 or min_content_length > max_content_length:
            self.logger.error(f"max_content_length or min_content_length not set or invalid max_content_length={max_content_length} min_content_length={min_content_length}")
            raise StateFieldError(node_name=self.name,field_name="max_content_lengrh or min_content_length",expected_type=int)
        #3 获取文档标题 给后续步骤作为兜底使用
        file_title=state.get("file_title","")

        #4 返回md_content 以及 文档标题 以及 配置信息
        return new_md_content, file_title, max_content_length, min_content_length
    def split_by_head(self,md_content:str,file_title:str)->List[dict[str,Any]]:
        """
        最终得到的产物是切成的块列表，分每一块是什么结构
        {
        "body":"内容正文",
        "title":"这块的标题",
        "parent_title":"这块的父标题",
        "file_title":"文件标题",
        }
        """
        #1 md_content 按照标题切分成块
        md_lines=md_content.split("\n")
        #定义匹配md标题 和代码块 的正则表达式
        heading_re=re.compile(r"^\s*(#{1,6})\s+(.+)")
        code_block_re=re.compile(r"^\s*```")
        in_code_block=False
        # 声明hiearrchy列表用于存储各级标题 目的是为了识别父标题
        hiearrchy=[""]*7
        #2 遍历md_content 使用正则表达式判断是否遇到标题 但要注意代码块的影响
        #声明变量body 用来存储当前标题块section的内容
        body_lines=[]
        #声明变量 用来表示当前标题的层级
        current_level=0
        #声明列表current_title 表示当前标题
        current_title=""
        #定义最终的块结构
        final_sections=[]
        #声明一个内部方法 用来将某个标题块进行收集
        def flush():
            """
                    最终得到的产物是切成的块列表，分每一块是什么结构
                    {
                    "body":"内容正文",
                    "title":"这块的标题",
                    "parent_title":"这块的父标题",
                    "file_title":"文件标题",
                    }
                    """
            #收集body的内容
            body="\n".join(body_lines)
            #收集当前标题
            title=current_title
            #判断是否存在current_title 是否存在body 如果都不存在 就不收集该段内容
            if not title and not body:
                return
            #如果title没有值 就用file_title进行兜底
            title=title if title else file_title
            #收集父标题
            parent_title=""
            for i in range(current_level-1,0,-1):
                if not hiearrchy[i]=="":
                    parent_title=hiearrchy[i]
                    break
            section={
                "body":body,
                "title":title,
                "parent_title":parent_title if parent_title else title,
                "file_title":file_title
                }
            #将section添加到final_sections中
            final_sections.append(section)
            
        for index, md_lines in enumerate(md_lines):
            #2.1 判断当前行是不是标题 并且不在代码块中
            if code_block_re.match(md_lines):
                in_code_block=not in_code_block
            match=heading_re.match(md_lines)
            if match and not in_code_block:
                # 代表当前行是标题行并且不在代码块中
                #2.1 如果是标题行 则将上一个标题对应的块内容收集起来
                flush()
                #2.2 设置新的当前标题
                current_title=md_lines
                #2.3 清空body列表 用来存储新的标题块的内容
                body_lines=[]
                #2.4 将当前标题添加到hiearrchy列表中 用来识别父标题 并且记录当前标题的层级
                # 获取当前标题的层级 取决于当前标题#的个数 
                current_level=len(match.group(1))
                # 将当前标题添加到hiearrchy数组中的current_level位置
                hiearrchy[current_level]=current_title
                #清空hisearrchy数组中current_level之后的所有元素 因为当前标题的层级已经改变了
                for i in range(current_level+1,7):
                    hiearrchy[i]=""
            else:
                # 代表当前行不是标题行或者在代码块中 将当前行的内容添加到当前sectioin的body中
                body_lines.append(md_lines)
        #遍历到最后找不到title时 就将前一个title的内容再收集起来
        flush()
        return final_sections

    def split_and_merge(self,sections_by_head:List[dict[str,Any]],max_chunk_length:int,min_chunk_length:int)->List[Dict[str,Any]]:
        # 1 声明current_section用于收集二次切分后的section
        current_section=[]
        #2 遍历sections_by_head  针对每一个section进行二次切分
        for section in sections_by_head:
            split_sections=self.split_long_section(section,max_chunk_length)
            current_section.extend(split_sections)
        # for section in current_section:
        #     print("标题：", section["title"])
        #     print("正文：", section["body"])
        #     print("-" * 50)
        #3 针对小的section进行合并
        final_sections=self.merge_short_section(current_section,min_chunk_length)
        #4 返回二次切分和合并的section
        return final_sections

    def split_long_section(self,sections_by_head:List[dict[str,Any]],max_chunk_length:int)->List[Dict[str,Any]]:
        #1 判断section的大小是否超过max长度
        #1.1 计算section长度 title+body
        #1.1.1 获取section的title
        title=sections_by_head.get("title")
        #防御型编程 如果title太长 直接去title的前八十的长度
        if len(title)>80:
            title=title[:80]
        title_prefix=title+"\n\n"
        #1.1.2 获取body
        body=sections_by_head.get("body")

        #对body中的表格进行处理
        if "<table>" in body:
            MarkdownTableLinearizer.process(body)
            sections_by_head["body"]=body

        #1.1.3 计算长度
        total_length=len(body)+len(title_prefix)
        #1.2 对total_length 进行判断
        if total_length<=max_chunk_length:
            #说明没有超过最大长度 不需要切分
            return[sections_by_head]
        #section的长度超过最大长度 则需要切分 使用langchain的recursivecharactertextsplitter进行切分
        text_splitter=RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""],
            chunk_size=max_chunk_length,
            keep_separator=True,
            chunk_overlap=0,)

        #使用text_splitter对body进行切分
        split_body_list=text_splitter.split_text(body)
        #判断切分后的内容是否有多个字符串
        if len(split_body_list)==1:
            #说明没有对当前的section进行切分
            return[sections_by_head]
        #如果切分成了多个字符串 我们需要遍历切分后的字符串列表 将每一个字符串组装成section
        result_sections=[]
        for index, split_body in enumerate(split_body_list):
            new_section={
                "body":split_body,
                "title":f"{title}_{index+1}",
                "parent_title":sections_by_head.get("parent_title"),
                "file_title":sections_by_head.get("file_title")
            }
            result_sections.append(new_section)
        return result_sections
    #合并短的section
    def merge_short_section(self,current_section:List[Dict[str,Any]],min_content_length:int)->List[Dict[str,Any]]:
        """
        2. 合并短的section
   1. 获取下标为0的section，记录为current_section
   2. 定义final_sections用于收集合并后的section列表
   3. 从下标为1的section遍历到最后，遍历出来的section记录为next_section
      1. 判断next_section与current_section是否是同源：parent_title是否相同
      2. 判断current_section的长度是否小于min_content_length
      3. 如果上面两个条件都满足，则使用current_section合并next_section：
         1. 使用"\n\n"拼接current_section与next_section的body
         2. 将合并后的section的标题title设置成它的parent_title，因为合并后细粒的标题就不合适，改成parent_title最合适
      4. 如果不满足，则表示当前section不需要合并：
         1. 将current_section添加到final_sections中
         2. 将current_section的游标指向next_section继续往下遍历
   4. 遍历完之后，将最后一个current_section添加到final_sections中
   5. 返回final_sections
    """
        #1 获取下标为0的section，记录为current_section
        sections=current_section
        if not sections:
            return []
        current_section=sections[0]
        #2. 定义final_sections用于收集合并后的section列表
        final_sections=[]
        #3. 从下标为1的section遍历到最后，遍历出来的section记录为next_section
        for i in range(1, len(sections)):
            next_section=sections[i]

            #3.1 判断next_section与current_section是否是同源：parent_title是否相同
            same_title=next_section["parent_title"]==current_section["parent_title"]
            #3.2 判断current_section的长度是否小于min_content_length
            current_length=len(current_section["body"])
            is_short=current_length<min_content_length
            #3.3 如果上面两个条件都满足，则使用current_section合并next_section：
            if same_title and is_short:
                #3.3.1. 使用"\n\n"拼接current_section与next_section的body
                current_section["body"]+="\n\n"+next_section["body"]
               #3.3.2. 将合并后的section的标题title设置成它的parent_title，因为合并后细粒的标题就不合适，改成parent_title最合适
                current_section["title"]=current_section["parent_title"]
            #4. 如果不满足，则表示当前section不需要合并：
            else:
                #4.1 将current_section添加到final_sections中
                final_sections.append(current_section)
                #4.2 将current_section的游标指向next_section继续往下遍历
                current_section=next_section
        #5. 遍历完之后，将最后一个current_section添加到final_sections中
        final_sections.append(current_section)
        #6. 返回final_sections
        return final_sections

    #将切分完的内容 组装成后续节点可以使用的chunks
    def assemble_chunks(self,final_sections:List[Dict[str,Any]])->List[Dict[str,Any]]:
        """
         原本的section
        {
                "body":"内容正文",
                "title":"这块的标题",
                "parent_title":"这块的父标题",
                "file_title":"文件标题",
                }

        真正需要的chunk
        {
        "content":"内容正文 是title拼接body的内容",
        "title":"这块的标题",
        "parent_title":"这块的父标题",
        "file_title":"文件标题",
        }
        """
        chunks=[]
        for section in final_sections:
            title=section.get("title","")
            body=section.get("body","")
            chunk={
                "content":title+"\n\n"+body,
                "title":title,
                "parent_title":section.get("parent_title",""),
                "file_title":section.get("file_title","")

            }
            chunks.append(chunk)
        return chunks
    #备份chunks
    def backup_chunks(self,chunks:List[Dict[str,Any]],state:ImportGraphState):
        #1 指定备份文件的路径
        #1.1 获取文件输出的目录
        md_path=state.get("md_path","")
        md_path_obj=Path(md_path)
        file_dir_obj=Path(state.get("file_dir",""))
        backup_dir=file_dir_obj/md_path_obj.stem
        #1.2 判断目录是否存在 如果不存在就创建目录
        backup_dir.mkdir(parents=True,exist_ok=True)
        #1.3 指定备份文件的路径
        backup_file_path=backup_dir/"chunks.json"
        #2 将chunk写入备份文件中
        try:
            with open(backup_file_path,"w",encoding="utf-8") as f:
                import json
                json.dump(chunks,f,ensure_ascii=False,indent=4)
        except Exception as e:
            self.logger.warning(f"{md_path_obj.stem},文件备份成json文件失败  但是不影响主流程")







            
            
        

if __name__=="__main__":
    #1 将new_md的内容读取出来
    document_split_node=DocumentSplitNode()
    md_path = r"D:/python/shopkeeper-brain/knowledge/processor/import_processor/output_dir/万用表RS-12的使用/万用表RS-12的使用_new.md"
    with open(md_path,"r",encoding="utf-8") as f:
        md_content=f.read()
    init_state={
        "md_path":md_path,
        "md_content":md_content,
        "file_title":"split_document",
        "file_dir":"D:\python\shopkeeper-brain\knowledge\processor\input_dir"

    }
    document_split_node.process(init_state)
