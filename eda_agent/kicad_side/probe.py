"""报告 pcbnew 版本，以及无界面导出 DSN / 导回 SES 的函数是否存在（M0 待验证项①）。"""

import json
import sys

import pcbnew

print(
    json.dumps(
        {
            "python": sys.version.split()[0],
            "pcbnew": pcbnew.Version(),
            "export_dsn": hasattr(pcbnew, "ExportSpecctraDSN"),
            "import_ses": hasattr(pcbnew, "ImportSpecctraSES"),
        }
    )
)
