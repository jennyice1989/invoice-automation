"""Four varied and authentic-logo A2Z graphic compositions without invented livestock."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import hashlib
LOGO=Path(__file__).parent/"static"/"a2z-aquariums-logo.png"
F="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
S={"square":(1080,1080),"portrait":(1080,1350),"story":(1080,1920)}
def render(headline,category,fmt,style):
    if fmt not in S:raise ValueError("Unsupported format")
    w,h=S[fmt];v=style%4;bg=(8,30,47);cream=(245,242,226)
    a=[(57,204,214),(245,130,110),(204,225,130),(111,184,238)][v]
    im=Image.new("RGB",(w,h),bg);d=ImageDraw.Draw(im)
    if v==0:
        d.rectangle((0,0,170,h),fill=a)
        d.polygon([(170,0),(w,0),(w,int(h*.32)),(170,int(h*.5))],fill=(17,70,80))
        x,y=225,int(h*.38);ink=cream;width=w-300
    elif v==1:
        d.polygon([(0,0),(w,0),(w,int(h*.45)),(0,int(h*.58))],fill=a)
        d.polygon([(0,int(h*.48)),(w,int(h*.37)),(w,int(h*.92)),(0,h)],fill=cream)
        x,y=90,int(h*.50);ink=bg;width=w-180
    elif v==2:
        for x0 in range(0,w,75):d.line((x0,0,x0,h),fill=(36,90,95),width=2)
        for y0 in range(0,h,75):d.line((0,y0,w,y0),fill=(36,90,95),width=2)
        d.rounded_rectangle((56,int(h*.27),w-56,int(h*.86)),radius=20,fill=(6,50,55),outline=a,width=8)
        x,y=100,int(h*.43);ink=cream;width=w-200
    else:
        d.ellipse((-400,-300,1250,int(h*.65)),fill=(42,94,148))
        d.rounded_rectangle((65,int(h*.35),w-65,int(h*.93)),radius=25,fill=cream)
        x,y=115,int(h*.48);ink=bg;width=w-230
    d.text((x,y-90),category.replace("_"," ").upper(),font=ImageFont.truetype(F,26),fill=a if v!=1 else bg)
    words=" ".join(headline.upper().split())[:105].split()
    for sz in range(76,31,-3):
        f=ImageFont.truetype(F,sz);lines=[];line=""
        for word in words:
            test=(line+" "+word).strip()
            if d.textbbox((0,0),test,font=f)[2]<=width:line=test
            else:
                if line:lines.append(line)
                line=word
        if line:lines.append(line)
        if len(lines)*(sz+13)<h*.33:break
    for line in lines:
        d.text((x,y),line,font=f,fill=ink);y+=sz+13
    if LOGO.exists():
        with Image.open(LOGO) as raw:
            logo=raw.convert("RGBA");logo.thumbnail((150,150))
            im.paste(logo,(w-65-logo.width,38),logo)
    out=BytesIO();im.save(out,"JPEG",quality=88);return out.getvalue()
