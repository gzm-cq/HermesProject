"""兼容 shim：knowledge_navigation.filtering → knowledge_navigation.core.filtering"""
from knowledge_navigation.core.filtering import *

# 旧函数名别名
from knowledge_navigation.core.filtering import filter_by_score as filter_results
from knowledge_navigation.core.filtering import format_context_lines as format_context
