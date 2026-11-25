import os
import sys
from pathlib import Path

# Local workflow traces stay on this computer unless the user configures a model endpoint.
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'

def error_hook(kind,value,tb):
    import traceback
    root=Path(os.environ.get('SKYGUARD_DATA_DIR',os.environ.get('LOCALAPPDATA','.')))/'SkyguardDesktop'
    root.mkdir(parents=True,exist_ok=True)
    with (root/'crash.log').open('a',encoding='utf8') as f:traceback.print_exception(kind,value,tb,file=f)
    try:
        from PySide6.QtWidgets import QMessageBox,QApplication
        if QApplication.instance():QMessageBox.critical(None,'程序遇到问题',str(value)+'\n详细信息已保存到本地日志。')
    except Exception:pass

sys.excepthook=error_hook
if __name__=='__main__':
    if '--diagnose' in sys.argv:
        from tempfile import mkdtemp
        from skyguard.diagnostics import run
        args = sys.argv[sys.argv.index('--diagnose')+1:]
        output = Path(args[0] if args else mkdtemp(prefix='skyguard-diagnose-'))
        raise SystemExit(run(output))
    else:
        from skyguard.ui import main
        raise SystemExit(main())
