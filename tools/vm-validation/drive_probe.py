import vgamepad as vg, time, json, sys
sys.path.insert(0,"C:\\rr")
import acshm
pad=vg.VX360Gamepad(); pad.reset(); pad.update(); time.sleep(1.0)
log=[]
def step(label,rt=0.0,lt=0.0,lx=0.0,dur=3.0):
    t0=time.time()
    while time.time()-t0<dur:
        pad.right_trigger_float(rt); pad.left_trigger_float(lt); pad.left_joystick_float(lx,0.0); pad.update()
        time.sleep(0.05)
    p=acshm.phys(); g=acshm.gfx()
    r=dict(label=label,speed=round(p["speed"],2),gear=p["gear"],rpm=p["rpm"],gas=p["gas"],brake=p["brake"],steer=round(p["steer"],2),inpit=g["inpit"],npos=round(g["npos"],5),xyz=[round(v,2) for v in g["xyz"]],cur=g["cur"])
    print(json.dumps(r)); log.append(r)
step("idle",dur=1)
step("gas40",rt=0.4,dur=4)
step("gas40_steerR",rt=0.4,lx=0.5,dur=3)
step("brake",lt=1.0,dur=3)
pad.reset(); pad.update()
json.dump(log,open("C:\\rr\\drive_probe.json","w"))
