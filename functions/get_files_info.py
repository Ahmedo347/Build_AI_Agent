import os

schema_get_files_info = {
    "type": "function",
    "function": {
        "name": "get_files_info",
        "description": "Lists files in a specified directory relative to the working directory, providing file size and directory status",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory path to list files from, relative to the working directory (default is the working directory itself)",
                },
            },
        },
    },
}

schema_get_file_content = {
    "type": "function",
    "function": {
        "name": "get_file_content",
        "description": "Reads and returns the content of a file within the working directory",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read, relative to the working directory",
                },
            },
            "required": ["file_path"],
        },
    },
}

def get_files_info(working_directory: str, directory: str = ".") -> str:
    
    working_dir_abs = os.path.abspath(working_directory)
    target_dir = os.path.normpath(os.path.join(working_dir_abs, directory))
    valid_target_dir = os.path.commonpath([working_dir_abs, target_dir]) == working_dir_abs
    if not os.path.isdir(target_dir):
        return f'Error: "{directory}" is not a directory'
    
    if not valid_target_dir:
        return f'Error: Cannot list "{directory}" as it is outside the permitted working directory'

    files_info = []
    for item in os.listdir(target_dir):
        item_path = os.path.join(target_dir, item)
        file_size = os.path.getsize(item_path)
        is_dir = os.path.isdir(item_path)
            
            
        files_info.append(f"- {item}: file_size={file_size} bytes, is_dir={is_dir}")
    
    return "\n".join(files_info)
    return f'Success: "{directory}" is within the working directory'
    
def get_file_content(working_directory: str, file_path: str) -> str:
        
    working_dir_abs_content = os.path.abspath(working_directory)
    target_file_path = os.path.normpath(os.path.join(working_dir_abs_content, file_path))
    valid_target_file_path = os.path.commonpath([working_dir_abs_content, target_file_path]) == working_dir_abs_content

    if not  valid_target_file_path:
         return f'Error: Cannot read "{file_path}" as it is outside the permitted working directory'
    
    if not os.path.isfile(target_file_path): 
         return f'Error: File not found or is not a regular file: "{file_path}"'
     
    MAX_CHARS = 10000
    
    with open(target_file_path, "r", encoding="utf-8") as f:
        content = f.read(MAX_CHARS)
    
        if f.read(1):
            content += f'\n[...File "{target_file_path}" truncated at {MAX_CHARS} characters]'    
    
    return content

    