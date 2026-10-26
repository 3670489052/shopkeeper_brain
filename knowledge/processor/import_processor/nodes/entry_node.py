import logging
from pathlib import Path

from knowledge.processor.import_processor.base import BaseNode
from knowledge.processor.import_processor.exceptions import StateFieldError, ValidationError
from knowledge.processor.import_processor.state import ImportGraphState


class EntryNode(BaseNode):
    name="entry_node"
    def process(self,state:ImportGraphState)->ImportGraphState:
        #1 获取state中的import_file_path file_dir 并且判断是否为空
        self.log_step(step_name="step_1",message="获取state中的import_file_path file_dir 并且判断是否为空")
        import_file_path=state.get("import_file_path")
        file_dir=state.get("file_dir")
        if not import_file_path :
            self.logger.error(f"import_file_path  not found in state {state}")
            raise StateFieldError(node_name=self.name,field_name="import_file_path",expected_type=str)
        if not file_dir:
            self.logger.error(f"file_dir not found in state {state}")
            raise StateFieldError(node_name=self.name,field_name="file_dir",expected_type=str)

        #2 判断file_dir import_file_path是否存在
        self.log_step(step_name="step_2",message="判断file_dir import_file_path是否存在")
        import_file_path_obj=Path(import_file_path)
        file_dir_obj=Path(file_dir)
        if not import_file_path_obj.exists() :
            self.logger.error(f"import_file_path not exist")
            raise StateFieldError(node_name=self.name,field_name="import_file_path",expected_type=Path)
        if not file_dir_obj.exists():
            self.logger.error(f"file_dir not exist")
            raise StateFieldError(node_name=self.name,field_name="file_dir",expected_type=Path)
        #3 判断文件类型
        #3.1 获取文件后缀
        self.log_step(step_name="step_3",message="获取文件后缀")
        suffix=import_file_path_obj.suffix
        #3.2 判断文件后缀是否为支持的类型
        if suffix==".pdf":
            #3.2.1 如果是pdf文件  则设置is_path_enable为True 设置pdf_path为import_file_path
            state["is_pdf_read_enabled"]=True
            state["pdf_path"]=import_file_path
        elif suffix==".md":
            #3.2.2 如果是md文件  则设置is_path_enable为True 设置md_path为import_file_path
            state["is_md_read_enabled"]=True
            state["md_path"]=import_file_path
        else :
            # 3.2.3 抛异常
            self.logger.error(f"unsupported file suffix {suffix}")
            raise ValidationError(message="unsupported file suffix",node_name=self.name )

        #获取文件标题  并且设置到state中
        self.log_step(step_name="step_4",message="获取文件标题  并且设置到state中")
        file_title=import_file_path_obj.stem
        state["file_title"]=file_title
        #4 将上述获取到的值设置到state中 并且返回state
        return state

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    entry_node=EntryNode()
    init_state={
        "import_file_path":"abc/vcd/ss.txt",

        "file_dir":"abc/vcd",
    }
    result=entry_node(init_state)
