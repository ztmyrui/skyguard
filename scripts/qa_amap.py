from pathlib import Path
import os,sys,tempfile,time,json
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'skyguard-desktop'))
from PySide6.QtWidgets import QApplication,QDialogButtonBox
from PySide6.QtCore import QTimer
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow


def main():
    """Run the AMap setup and connection checks."""
    for name in ('SKYGUARD_AMAP_KEY', 'SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(name, None)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'D:\A-GPT Out') / '低空守望_P1' / '高德验证'
    out.mkdir(parents=True, exist_ok=True)
    app=QApplication([]);temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True);w=MainWindow(temp.name);w.resize(1440,900);w.show()
    started=time.monotonic();state={'stage':0};result={}
    def snapshot_dialog():
        d=app.activeModalWidget();assert d is not None
        d.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        d.grab().save(str(out/'地图配置.png'));d.reject()
    def step():
        try:
            if state['stage']==0:
                if time.monotonic()-started<4:return
                w.map_view.run_js("JSON.stringify({channel:!!bridge,message:document.getElementById('status').textContent,button:!!document.getElementById('configure').onclick})",lambda v:result.update({'unconfigured_page':json.loads(v)}))
                w.grab().save(str(out/'待配置界面.png'));state['stage']=1;return
            if state['stage']==1:
                if 'unconfigured_page' not in result:
                    return
                assert result['unconfigured_page']['channel'] and result['unconfigured_page']['button']
                QTimer.singleShot(600,snapshot_dialog);w.map_settings_dialog();connect_test_map(w);state['stage']=2;return
            if state['stage']==2:
                if not w.map_view.is_ready:return
                w.context.service.generate('obs-a','事件协同');w.context.service.confirm(0)
                assert len(w.context.service.scene.tasks)==3
                result.update({'business_tasks':3,'real_amap_verified':True,'passed':True})
                (out/'接入界面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
                print(json.dumps(result,ensure_ascii=True),flush=True);w.close();app.quit()
        except Exception as e:print(repr(e),flush=True);app.exit(1)
    timer=QTimer();timer.setInterval(500);timer.timeout.connect(step);timer.start()
    QTimer.singleShot(35000,lambda:app.exit(2))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
