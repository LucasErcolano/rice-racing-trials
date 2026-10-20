import mmap, struct, ctypes, time, json, sys
def _page(name,size):
    m=mmap.mmap(-1,size,name,access=mmap.ACCESS_READ); d=m.read(size); m.close(); return d
def phys():
    d=_page("Local\\acpmf_physics",800)
    pid,gas,brake,fuel,gear,rpm,steer,spd=struct.unpack_from("<iffiiiff",d,0)
    vel=struct.unpack_from("<3f",d,32); acc=struct.unpack_from("<3f",d,44)
    return dict(pid=pid,gas=gas,brake=brake,gear=gear,rpm=rpm,steer=steer,speed=spd,vel=vel,acc=acc)
def gfx():
    d=_page("Local\\acpmf_graphics",1600)
    pid,status,session=struct.unpack_from("<iii",d,0)
    s=lambda o:d[o:o+30].decode("utf-16le",errors="ignore").split("\0")[0]
    laps,pos,icur,ilast,ibest=struct.unpack_from("<iiiii",d,132)
    tleft,dist,inpit,sector,lastsec,nlaps=struct.unpack_from("<ffiiii",d,152)
    rtm,npos,active=struct.unpack_from("<ffi",d,244)
    pidx=struct.unpack_from("<i",d,256+720+240)[0]
    coords=struct.unpack_from("<3f",d,256)
    return dict(status=status,session=session,cur=s(12),last=s(42),best=s(72),laps=laps,icur=icur,ilast=ilast,ibest=ibest,
        dist=dist,inpit=inpit,sector=sector,npos=npos,active=active,xyz=coords,playerIdx=pidx)
if __name__=="__main__":
    n=int(sys.argv[1]) if len(sys.argv)>1 else 1
    for _ in range(n):
        print(json.dumps(dict(p=phys(),g=gfx()))); time.sleep(0.5)
