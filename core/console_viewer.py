"""Local, read-only view of public v0.2 episode observations."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit

from contracts.data_v02 import EpisodeSnapshotV02


_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>飞行状态</title><style>
:root{font-family:system-ui,sans-serif;color:#222b30;background:#f8f9f9}*{box-sizing:border-box}body{margin:0}header{background:#263238;color:white;padding:16px max(20px,calc((100vw - 1100px)/2))}h1{font-size:20px;margin:0}main{max-width:1100px;margin:auto;padding:24px 20px;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px}section{padding:0;min-width:0}section+section{border-left:1px solid #d1d8da;padding-left:24px}h2{font-size:15px;margin:0 0 14px}#status{font-weight:700}#note{color:#526066;font-size:13px}canvas{width:100%;height:340px;display:block;background:#f1f4f4;border:1px solid #d1d8da}#vehicles{display:grid;gap:12px}.vehicle{border-top:1px solid #d1d8da;padding-top:10px;overflow-wrap:anywhere}dl{display:grid;grid-template-columns:90px minmax(0,1fr);gap:5px;margin:8px 0;font-size:13px}dd{margin:0;font-variant-numeric:tabular-nums;text-align:right;overflow-wrap:anywhere}img{max-width:100%;height:auto;display:block}#frame{margin-top:18px}#frame p{font-size:12px;color:#526066} @media(max-width:720px){main{grid-template-columns:1fr;padding:16px;gap:24px}section+section{border-left:0;border-top:1px solid #d1d8da;padding:20px 0 0}canvas{height:290px}}
</style><header><h1>飞行状态</h1></header><main><section><h2>位置轨迹 · 北 / 东（米）</h2><canvas id="plot"></canvas><p id="note">等待初始观测</p><div id="frame"></div></section><section><h2 id="status">等待连接</h2><div id="vehicles"></div></section></main><script>
const plot=document.getElementById('plot'),ctx=plot.getContext('2d'),colors=['#008b78','#d35439','#4c65b3','#aa762b'];
function draw(data){let w=plot.clientWidth,h=plot.clientHeight,dpr=devicePixelRatio||1;plot.width=w*dpr;plot.height=h*dpr;ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);let vehicles=Object.values(data.vehicles),goals=vehicles.filter(v=>v.target).map(v=>v.target),all=vehicles.flatMap(v=>v.track).concat(goals);if(!all.length)return;let ns=all.map(p=>p[0]),es=all.map(p=>p[1]),n0=Math.min(...ns),n1=Math.max(...ns),e0=Math.min(...es),e1=Math.max(...es),span=Math.max(n1-n0,e1-e0,2),cx=(n0+n1)/2,cy=(e0+e1)/2,pad=35,scale=Math.min((w-2*pad)/span,(h-2*pad)/span);let pos=p=>[w/2+(p[1]-cy)*scale,h/2-(p[0]-cx)*scale];ctx.strokeStyle='#dce6e0';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(w/2,0);ctx.lineTo(w/2,h);ctx.moveTo(0,h/2);ctx.lineTo(w,h/2);ctx.stroke();Object.entries(data.vehicles).forEach(([id,v],i)=>{let pts=v.track;if(pts.length){ctx.strokeStyle=colors[i%colors.length];ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=3;ctx.beginPath();pts.forEach((p,j)=>{let [x,y]=pos(p);j?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();let [x,y]=pos(pts.at(-1));ctx.beginPath();ctx.arc(x,y,5,0,7);ctx.fill();ctx.font='13px system-ui';ctx.fillText(id,x+9,y-7)}if(v.target){let [x,y]=pos(v.target);ctx.strokeStyle='#b53328';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,8,0,7);ctx.moveTo(x-12,y);ctx.lineTo(x+12,y);ctx.moveTo(x,y-12);ctx.lineTo(x,y+12);ctx.stroke();ctx.fillStyle='#8c251e';ctx.font='13px system-ui';ctx.fillText('目标 '+id,x+14,y+20)}})}
function row(label,value){let dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=typeof value==='number'&&label!=='序号'?value.toFixed(2):value;return[dt,dd]}
async function refresh(){try{let r=await fetch('/state',{cache:'no-store'});let d=await r.json();document.getElementById('status').textContent=d.status+(d.result?' · '+d.result:'');document.getElementById('note').textContent=d.mock?'Mock 示意轨迹 · 来源：公开观测':'轨迹来源：公开观测；相机按最新观测更新';let box=document.getElementById('vehicles');box.replaceChildren();Object.entries(d.vehicles).forEach(([id,v],i)=>{let div=document.createElement('div'),title=document.createElement('strong'),dl=document.createElement('dl');div.className='vehicle';title.textContent=id;title.style.color=colors[i%colors.length];div.append(title);for(let [a,b] of [['北 N',v.north],['东 E',v.east],['下 D',v.down],['序号',v.sequence],['传感器',v.sensors],['缺失',v.missing]])dl.append(...row(a,b??'—'));if(v.target)dl.append(...row('目标 N/E/D',v.target.map(x=>x.toFixed(2)).join(' / ')));div.append(dl);box.append(div)});draw(d);let frame=document.getElementById('frame');frame.replaceChildren();if(d.frame){let h=document.createElement('h2'),img=document.createElement('img'),p=document.createElement('p');h.textContent='最新 RGB 帧 · '+d.frame.vehicle;img.src='/image?path='+encodeURIComponent(d.frame.path);img.alt='最新一次公开 RGB 传感器图像';p.textContent='采集时间 '+new Date(d.frame.captured_wall_time_ns/1e6).toLocaleString();frame.append(h,img,p)}else if(!d.mock){let p=document.createElement('p');p.textContent='当前观测没有可用的 RGB 帧；查看传感器缺失状态。';frame.append(p)}}catch(e){document.getElementById('status').textContent='页面连接已关闭'}}
refresh();setInterval(refresh,700);
</script></html>"""


class LiveViewer:
    def __init__(self, *, mock: bool) -> None:
        self._lock = Lock()
        self._episode_root: Path | None = None
        self._data: dict[str, object] = {"status": "等待连接", "result": None,
                                         "mock": mock, "vehicles": {}, "frame": None}
        viewer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlsplit(self.path)
                if url.path == "/":
                    self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif url.path == "/state":
                    with viewer._lock:
                        payload = json.dumps(viewer._data, ensure_ascii=False).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                elif url.path == "/image":
                    values = parse_qs(url.query).get("path", [])
                    content = viewer._image(values[0]) if len(values) == 1 else None
                    if content is None:
                        self._send(404, b"Not found", "text/plain")
                    else:
                        self._send(200, content, "image/png")
                else:
                    self._send(404, b"Not found", "text/plain")

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/"

    def observe(self, snapshot: EpisodeSnapshotV02, episode_root: Path) -> None:
        with self._lock:
            self._episode_root = episode_root.resolve()
            vehicles = self._data["vehicles"]
            latest = None
            for vehicle_id, obs in snapshot.observations.items():
                entry = vehicles.setdefault(vehicle_id, {"track": []})
                state = obs.state
                sensors = [f"{sensor.kind.rsplit('/', 1)[-1].upper()}:{sensor.sensor_id}"
                           for sensor in obs.sensors]
                entry.update(north=state.get("north_m"), east=state.get("east_m"),
                             down=state.get("down_m"), sequence=obs.sequence,
                             sensors=", ".join(sensors) or "无",
                             missing=", ".join(f"{name}: {reason}"
                                                for name, reason in obs.missing_sensors.items()) or "无")
                target = state.get("target")
                if isinstance(target, dict):
                    values = [target.get(key) for key in ("north_m", "east_m", "down_m")]
                    if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and math.isfinite(value) for value in values):
                        entry["target"] = values
                north, east = state.get("north_m"), state.get("east_m")
                if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       for value in (north, east)):
                    entry["track"].append([north, east])
                for sensor in obs.sensors:
                    if sensor.kind == "drone/rgb" and sensor.relative_path.lower().endswith(".png"):
                        if latest is None or sensor.captured_wall_time_ns > latest["captured_wall_time_ns"]:
                            latest = {"vehicle": vehicle_id, "path": sensor.relative_path,
                                      "captured_wall_time_ns": sensor.captured_wall_time_ns}
            self._data["frame"] = latest
            self._data["status"] = f"运行中 · 观测 {snapshot.sequence}"

    def finish(self, result: str) -> None:
        with self._lock:
            self._data["status"] = "运行结束"
            self._data["result"] = result

    def event(self, event: dict[str, object]) -> None:
        kind = event.get("kind")
        fields = event.get("fields", {})
        with self._lock:
            if kind == "action":
                self._data["status"] = f"执行中 · {fields.get('vehicle_id', '?')} · {fields.get('action_kind', '?')}"
            elif kind == "execution":
                self._data["status"] = ("动作完成" if fields.get("succeeded") else "动作失败")
            elif kind == "termination":
                self._data["status"] = "飞行结束 · 正在整理结果"

    def _image(self, relative: str) -> bytes | None:
        with self._lock:
            frame = self._data["frame"]
            root = self._episode_root
            if root is None or not frame or relative != frame["path"]:
                return None
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".png":
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def main() -> None:
    import argparse
    from threading import Event

    parser = argparse.ArgumentParser(description="Preview a recorded public v0.2 snapshot locally")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    episode = args.episode.resolve()
    lines = (episode / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    if not lines:
        parser.error("episode has no recorded trajectory")
    viewer = LiveViewer(mock=args.mock)
    try:
        for index, line in enumerate(lines):
            record = json.loads(line)
            if index == 0:
                viewer.observe(EpisodeSnapshotV02.from_dict(record["before"]), episode)
            viewer.observe(EpisodeSnapshotV02.from_dict(record["after"]), episode)
        viewer.finish("已保存的运行记录")
        print(viewer.url, flush=True)
        Event().wait()
    finally:
        viewer.close()


if __name__ == "__main__":
    main()
