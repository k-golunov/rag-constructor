"""Generate matching BPMN 2.0 + PNG diagrams using the same layout."""
from pathlib import Path
import math
import textwrap
import xml.etree.ElementTree as E
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
NS = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
      'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
      'dc': 'http://www.omg.org/spec/DD/20100524/DC',
      'di': 'http://www.omg.org/spec/DD/20100524/DI'}
for k, v in NS.items():
    E.register_namespace(k, v)

def el(parent, tag, **attrs):
    ns, name = tag.split(':')
    return E.SubElement(parent, '{%s}%s' % (NS[ns], name), {k: str(v) for k, v in attrs.items()})

class Diagram:
    def __init__(self, slug, title, subtitle, size):
        self.slug, self.size = slug, size
        self.nodes, self.flows, self.pools, self.notes = {}, [], [], []
        self.im = Image.new('RGB', size, '#ffffff')
        self.d = ImageDraw.Draw(self.im)
        self.font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 21)
        self.small = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
        self.bold = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 30)
        self.d.text((50, 32), title, font=self.bold, fill='#142d4e')
        self.d.text((50, 84), subtitle, font=self.font, fill='#4c6077')

    def pool(self, pid, name, box):
        self.pools.append((pid, name, box))
        return pid

    def node(self, pid, ident, kind, name, x, y, w=220, h=90):
        if kind in ('startEvent', 'endEvent'):
            w = h = 42
        elif kind == 'exclusiveGateway':
            w = h = 60
        self.nodes[ident] = dict(pid=pid, kind=kind, name=name, box=(x,y,w,h))
        return ident

    def port(self, ident, side):
        x,y,w,h = self.nodes[ident]['box']
        return {'L':(x,y+h/2), 'R':(x+w,y+h/2), 'T':(x+w/2,y), 'B':(x+w/2,y+h)}[side]

    def flow(self, a, b, label='', sides=('R','L'), via=(), label_at=None, default=False):
        pts = [self.port(a,sides[0]), *via, self.port(b,sides[1])]
        self.flows.append(dict(a=a,b=b,label=label,pts=pts,label_at=label_at,default=default))

    def note(self, pid, ident, text, box, target):
        self.notes.append((pid,ident,text,box,target))

    def wrapped(self, text, box, font=None, fill='#142d4e'):
        font = font or self.font
        x,y,w,h = box
        lines=[]
        for para in text.split('\n'):
            words=para.split(); line=''
            for word in words:
                test=(line+' '+word).strip()
                if self.d.textlength(test,font=font)>w-18 and line:
                    lines.append(line); line=word
                else:
                    line=test
            lines.append(line)
        lh=font.size+4
        assert len(lines)*lh <= h+2, (text,box,len(lines)*lh)
        for i,line in enumerate(lines):
            tw=self.d.textlength(line,font=font)
            self.d.text((x+(w-tw)/2,y+(h-len(lines)*lh)/2+i*lh),line,font=font,fill=fill)

    def save(self):
        root=E.Element('{%s}definitions'%NS['bpmn'],id=self.slug,
                       targetNamespace='https://rag-constructor.local/bpmn',
                       exporter='RAG Constructor',exporterVersion='1.0')
        coll=el(root,'bpmn:collaboration',id='Collaboration')
        procs={}
        dia=el(root,'bpmndi:BPMNDiagram',id='Diagram')
        plane=el(dia,'bpmndi:BPMNPlane',id='Plane',bpmnElement='Collaboration')
        def shape(ident,box,**attrs):
            sh=el(plane,'bpmndi:BPMNShape',id='DI_'+ident,bpmnElement=ident,**attrs)
            x,y,w,h=box
            el(sh,'dc:Bounds',x=x,y=y,width=w,height=h)
            return sh
        for pid,name,box in self.pools:
            proc=el(root,'bpmn:process',id=pid,isExecutable='false',name=name)
            procs[pid]=proc
            el(coll,'bpmn:participant',id='Participant_'+pid,name=name,processRef=pid)
            shape('Participant_'+pid,box,isHorizontal='true')
            x,y,w,h=box
            self.d.rectangle((x,y,x+w,y+h),outline='#8395ab',width=2)
            self.d.rectangle((x,y,x+w,y+48),fill='#eaf0f7',outline='#8395ab',width=2)
            self.d.text((x+18,y+10),name,font=self.font,fill='#142d4e')
        xmlnodes={}
        for ident,n in self.nodes.items():
            xmlnodes[ident]=el(procs[n['pid']],'bpmn:'+n['kind'],id=ident,name=n['name'])
            if n['kind']=='startEvent' and self.slug=='hw3-model-switch':
                ev=el(xmlnodes[ident],'bpmn:timerEventDefinition',id='WeeklyTimer')
                el(ev,'bpmn:timeCycle').text='R/P7D'
        for i,f in enumerate(self.flows):
            fid='Flow_'+str(i+1)
            assert self.nodes[f['a']]['pid']==self.nodes[f['b']]['pid']
            attrs=dict(id=fid,sourceRef=f['a'],targetRef=f['b'])
            if f['label']: attrs['name']=f['label']
            fe=el(procs[self.nodes[f['a']]['pid']],'bpmn:sequenceFlow',**attrs)
            if f['default']:
                xmlnodes[f['a']].set('default',fid)
            elif self.nodes[f['a']]['kind']=='exclusiveGateway' and f['label']:
                el(fe,'bpmn:conditionExpression').text=f['label']
            el(xmlnodes[f['a']],'bpmn:outgoing').text=fid
            el(xmlnodes[f['b']],'bpmn:incoming').text=fid
            edge=el(plane,'bpmndi:BPMNEdge',id='DI_'+fid,bpmnElement=fid)
            for px,py in f['pts']: el(edge,'di:waypoint',x=px,y=py)
            self.d.line(f['pts'],fill='#455c76',width=3)
            ax,ay=f['pts'][-2]; bx,by=f['pts'][-1]
            theta=math.atan2(by-ay,bx-ax)
            arrow=[(bx,by),(bx-13*math.cos(theta-.42),by-13*math.sin(theta-.42)),
                   (bx-13*math.cos(theta+.42),by-13*math.sin(theta+.42))]
            self.d.polygon(arrow,fill='#455c76')
            if f['default']:
                ax,ay=f['pts'][0]; bx,by=f['pts'][1]
                ang=math.atan2(by-ay,bx-ax)
                cx,cy=ax+13*math.cos(ang),ay+13*math.sin(ang)
                self.d.line((cx-6,cy-6,cx+6,cy+6),fill='#455c76',width=2)
            if f['label']:
                lx,ly=f['label_at'] or ((f['pts'][0][0]+f['pts'][1][0])/2,
                                          (f['pts'][0][1]+f['pts'][1][1])/2-20)
                tw=self.d.textlength(f['label'],font=self.small)
                self.d.rectangle((lx-tw/2-5,ly-3,lx+tw/2+5,ly+23),fill='white')
                self.d.text((lx-tw/2,ly),f['label'],font=self.small,fill='#314862')
                lab=el(edge,'bpmndi:BPMNLabel')
                el(lab,'dc:Bounds',x=lx-tw/2,y=ly,width=tw,height=24)
        for ident,n in self.nodes.items():
            x,y,w,h=n['box']; kind=n['kind']
            sh=shape(ident,n['box'],**({'isMarkerVisible':'true'} if kind=='exclusiveGateway' else {}))
            if kind in ('startEvent','endEvent'):
                self.d.ellipse((x,y,x+w,y+h),fill='#edf7ef' if kind=='startEvent' else '#fff0ed',
                               outline='#2c6450' if kind=='startEvent' else '#9e4a3e',
                               width=2 if kind=='startEvent' else 5)
                if kind=='startEvent' and self.slug=='hw3-model-switch':
                    self.d.ellipse((x+8,y+8,x+w-8,y+h-8),outline='#2c6450',width=2)
                    self.d.line((x+21,y+11,x+21,y+21,x+29,y+24),fill='#2c6450',width=2)
                box=(x-80,y+h+8,w+160,65)
            elif kind=='exclusiveGateway':
                self.d.polygon([(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)],
                               fill='#fff5da',outline='#94702a',width=2)
                self.d.line((x+22,y+22,x+38,y+38),fill='#71531b',width=3)
                self.d.line((x+38,y+22,x+22,y+38),fill='#71531b',width=3)
                box=(x-95,y-63,w+190,58)
            else:
                self.d.rounded_rectangle((x,y,x+w,y+h),radius=12,fill='#f1f6fc',outline='#416b98',width=2)
                marker='ЧЕЛОВЕК' if kind=='userTask' else 'СИСТЕМА'
                self.d.text((x+12,y+6),marker,font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',12),fill='#667f9c')
                box=(x+4,y+20,w-8,h-22)
            self.wrapped(n['name'],box,self.small if kind not in ('startEvent','endEvent','exclusiveGateway') else self.font)
            if kind in ('startEvent','endEvent','exclusiveGateway'):
                lab=el(sh,'bpmndi:BPMNLabel'); xx,yy,ww,hh=box
                el(lab,'dc:Bounds',x=xx,y=yy,width=ww,height=hh)
        for pid,ident,txt,box,target in self.notes:
            anno=el(procs[pid],'bpmn:textAnnotation',id=ident)
            el(anno,'bpmn:text').text=txt
            shape(ident,box)
            x,y,w,h=box
            self.d.rectangle((x,y,x+w,y+h),fill='#faf8ed')
            self.d.line((x+12,y,x,y,x,y+h,x+12,y+h),fill='#9b8c5b',width=2)
            self.wrapped(txt,(x+10,y+4,w-20,h-8),self.small)
            assoc=el(procs[pid],'bpmn:association',id='Association_'+ident,sourceRef=target,targetRef=ident)
            edge=el(plane,'bpmndi:BPMNEdge',id='DI_Association_'+ident,bpmnElement=assoc.get('id'))
            a=self.port(target,'B'); b=(x+w/2,y)
            for px,py in (a,b): el(edge,'di:waypoint',x=px,y=py)
            # Keep annotation connectors outside the routed process flows.
            self.d.line((x+w/2,y-12,x+w/2,y),fill='#9b8c5b',width=2)
        # BPMN FlowNode children must list incoming before outgoing; timer
        # definitions follow these inherited children.
        for node in xmlnodes.values():
            children=list(node)
            for child in children:
                node.remove(child)
            for local in ('incoming','outgoing'):
                for child in children:
                    if child.tag=='{%s}%s'%(NS['bpmn'],local): node.append(child)
            for child in children:
                if child.tag not in ('{%s}incoming'%NS['bpmn'],'{%s}outgoing'%NS['bpmn']): node.append(child)
        # BPMN schema sequence: processes before collaboration and diagram.
        root.remove(coll); root.remove(dia); root.append(coll); root.append(dia)
        E.indent(root,space='  ')
        E.ElementTree(root).write(OUT/(self.slug+'.bpmn'),encoding='utf-8',xml_declaration=True)
        self.im.save(OUT/(self.slug+'.png'))
        self.validate()

    def validate(self):
        for pid,_,_ in self.pools:
            nodes={k:v for k,v in self.nodes.items() if v['pid']==pid}
            starts=[k for k,v in nodes.items() if v['kind']=='startEvent']
            seen=set(starts); queue=starts[:]
            while queue:
                a=queue.pop()
                for f in self.flows:
                    if f['a']==a and f['b'] not in seen:
                        seen.add(f['b']); queue.append(f['b'])
            assert seen==set(nodes), ('Unreachable',set(nodes)-seen)
            for ident,n in nodes.items():
                outgoing=[f for f in self.flows if f['a']==ident]
                assert outgoing or n['kind']=='endEvent',ident
        print(self.slug, len(self.nodes), 'nodes;',len(self.flows),'flows; OK')

def hw2():
    d=Diagram('hw2-operation','ДЗ 2 · Эксплуатация RAG-ассистента',
              'Текущее поведение проекта · подготовка документов и обработка одного вопроса', (2020,1690))
    p=d.pool('Preparation','1. Администратор и RAG-система — подготовка базы знаний',(50,160,1920,470))
    d.node(p,'p_start','startEvent','Новые материалы',130,292)
    d.node(p,'p_create','userTask','Создать проект; задать LLM и эмбеддер',260,267)
    d.node(p,'p_upload','userTask','Загрузить документ через интерфейс',540,267)
    d.node(p,'p_index','serviceTask','Парсинг → чанки → эмбеддинги → Qdrant',820,267,260)
    d.node(p,'p_ok','exclusiveGateway','Обработка успешна?',1170,282)
    d.node(p,'p_ready','serviceTask','Установить статус источника completed',1330,267)
    d.node(p,'p_end','endEvent','База знаний готова',1750,292)
    d.node(p,'p_fail','serviceTask','Сообщить об ошибке обработки документа',1330,470)
    d.node(p,'p_badend','endEvent','Документ не готов',1750,494)
    for a,b in [('p_start','p_create'),('p_create','p_upload'),('p_upload','p_index'),('p_index','p_ok')]: d.flow(a,b)
    d.flow('p_ok','p_ready','Да',label_at=(1280,280)); d.flow('p_ready','p_end')
    d.flow('p_ok','p_fail','Нет',('B','L'),[(1200,515)],(1210,402),True)
    d.flow('p_fail','p_badend')
    d.note(p,'PrepNote','Обобщён сценарий одиночной загрузки. Ошибка может возникнуть при чтении, векторизации или записи в индекс.',(240,470,780,105),'p_index')
    p=d.pool('Question','2. Пользователь и RAG-система — ответ по подготовленной базе знаний',(50,710,1920,910))
    specs=[('q_start','startEvent','Нужна информация',110,857,220),
           ('q_ask','userTask','Задать вопрос в чате проекта',230,833,220),
           ('q_history','serviceTask','Проверить настройки; загрузить историю',490,833,220),
           ('q_search','serviceTask','Векторизовать вопрос; найти фрагменты',750,833,220),
           ('q_searchok','exclusiveGateway','Поиск выполнен?',1030,848,60),
           ('q_context','exclusiveGateway','Фрагменты найдены?',1250,848,60),
           ('q_build','serviceTask','Собрать контекст и список источников',1470,833,220),
           ('q_empty','serviceTask','Пустой контекст; статус no_context',1470,1030,220),
           ('q_merge','exclusiveGateway','',1770,848,60),
           ('q_generate','serviceTask','Вызвать LLM: вопрос, контекст и история',1690,1250,220),
           ('q_genok','exclusiveGateway','Генерация успешна?',1430,1265,60),
           ('q_save','serviceTask','Сохранить вопрос, ответ и метаданные',1120,1250,220),
           ('q_show','serviceTask','Вернуть ответ и источники пользователю',840,1250,220),
           ('q_end','endEvent','Ответ получен',700,1274,42),
           ('q_searcherror','serviceTask','Вернуть ошибку поиска (HTTP 502)',950,1030,220),
           ('q_searchend','endEvent','Без вызова LLM',800,1054,42),
           ('q_generror','serviceTask','Вернуть ошибку LLM (HTTP 502)',1350,1460,220),
           ('q_genend','endEvent','Ответ не сохранён',1210,1484,42)]
    for ident,kind,name,x,y,w in specs: d.node(p,ident,kind,name,x,y,w)
    for a,b in [('q_start','q_ask'),('q_ask','q_history'),('q_history','q_search'),('q_search','q_searchok')]: d.flow(a,b)
    d.flow('q_searchok','q_context','Да',label_at=(1160,850))
    d.flow('q_searchok','q_searcherror','Нет',('B','T'),label_at=(1085,975),default=True)
    d.flow('q_searcherror','q_searchend',sides=('L','R'))
    d.flow('q_context','q_build','Да',label_at=(1390,850))
    d.flow('q_context','q_empty','Нет',('B','L'),[(1280,1075)],(1300,978),True)
    d.flow('q_build','q_merge')
    d.flow('q_empty','q_merge',sides=('R','R'),via=[(1900,1075),(1900,878)])
    d.flow('q_merge','q_generate',sides=('B','T'),via=[(1800,1180)])
    d.flow('q_generate','q_genok',sides=('L','R'))
    d.flow('q_genok','q_save','Да',('L','R'),label_at=(1385,1263))
    d.flow('q_save','q_show',sides=('L','R')); d.flow('q_show','q_end',sides=('L','R'))
    d.flow('q_genok','q_generror','Нет',('B','T'),label_at=(1485,1385),default=True)
    d.flow('q_generror','q_genend',sides=('L','R'))
    d.d.text((60,1640),'Круг — событие · скруглённый блок — задача · ромб с X — исключающая развилка / объединение',font=d.small,fill='#4c6077')
    d.save()

def hw3():
    d=Diagram('hw3-model-switch','ДЗ 3 · Автоматическая смена LLM в RAG-проекте',
              'Предлагаемый процесс · только схема · эмбеддер, документы и индекс остаются прежними', (2240,1740))
    p=d.pool('Switch','Сервис контроля качества и переключения моделей — проектируемый процесс',(50,160,2140,1490))
    specs=[('s_start','startEvent','Еженедельный таймер',130,357,42),
           ('s_load','serviceTask','Загрузить эталонные вопросы и ограничения',270,333,220),
           ('s_base','serviceTask','Оценить текущую LLM на эталонных вопросах',570,333,220),
           ('s_need','exclusiveGateway','Качество ниже порога?',890,348,60),
           ('s_snapshot','serviceTask','Сохранить текущую конфигурацию для отката',1100,333,220),
           ('s_choose','serviceTask','Выбрать следующую LLM из списка кандидатов',1440,333,220),
           ('s_available','exclusiveGateway','Есть кандидат и ресурс?',1780,348,60),
           ('s_stable','endEvent','Смена не требуется',899,560,42),
           ('s_stop','serviceTask','Записать причину остановки; сохранить текущую LLM',1700,550,260),
           ('s_stopend','endEvent','Без смены модели',1550,574,42),
           ('s_eval','serviceTask','Оценить кандидата: качество, задержка, стоимость',1700,820,260),
           ('s_accept','exclusiveGateway','Все критерии выполнены?',1440,835,60),
           ('s_switch','serviceTask','Переключить LLM проекта на кандидата',1110,820,220),
           ('s_smoke','serviceTask','Выполнить контрольные запросы после смены',820,820,220),
           ('s_smokeok','exclusiveGateway','Контроль пройден?',600,835,60),
           ('s_record','serviceTask','Сохранить версию, метрики и результат смены',300,820,220),
           ('s_success','endEvent','Смена завершена',130,844,42),
           ('s_reject','serviceTask','Записать отказ кандидату или ошибку проверки',1700,1100,260),
           ('s_retry','exclusiveGateway','Есть ещё попытки и бюджет?',2030,1115,60),
           ('s_exhausted','serviceTask','Зафиксировать неуспех; оставить прежнюю LLM',1940,1340,220),
           ('s_exhaustend','endEvent','Лимиты исчерпаны',2030,1530,42),
           ('s_rollback','serviceTask','Восстановить предыдущую конфигурацию LLM',520,1100,220),
           ('s_alert','serviceTask','Записать сбой; уведомить ответственного',520,1320,220),
           ('s_rollbackend','endEvent','Откат выполнен',609,1530,42)]
    for ident,kind,name,x,y,w in specs: d.node(p,ident,kind,name,x,y,w)
    for a,b in [('s_start','s_load'),('s_load','s_base'),('s_base','s_need')]: d.flow(a,b)
    d.flow('s_need','s_snapshot','Да',label_at=(1020,350))
    d.flow('s_need','s_stable','Нет',('B','T'),label_at=(950,470),default=True)
    d.flow('s_snapshot','s_choose'); d.flow('s_choose','s_available')
    d.flow('s_available','s_stop','Нет',('B','T'),via=[(1810,500),(1830,500)],label_at=(1840,465),default=True)
    d.flow('s_stop','s_stopend',sides=('L','R'))
    d.flow('s_available','s_eval','Да',('R','R'),via=[(2000,378),(2000,865)],label_at=(2025,720))
    d.flow('s_eval','s_accept',sides=('L','R'))
    d.flow('s_accept','s_switch','Да',('L','R'),label_at=(1378,835))
    d.flow('s_switch','s_smoke',sides=('L','R')); d.flow('s_smoke','s_smokeok',sides=('L','R'))
    d.flow('s_smokeok','s_record','Да',('L','R'),label_at=(560,835))
    d.flow('s_record','s_success',sides=('L','R'))
    d.flow('s_accept','s_reject','Нет',('B','L'),via=[(1470,1145)],label_at=(1495,1000),default=True)
    d.flow('s_reject','s_retry')
    d.flow('s_retry','s_choose','Да',('R','T'),via=[(2150,1145),(2150,240),(1550,240)],label_at=(2148,1000))
    d.flow('s_retry','s_exhausted','Нет',('B','T'),via=[(2060,1280),(2050,1280)],label_at=(2090,1245),default=True)
    d.flow('s_exhausted','s_exhaustend',sides=('B','T'))
    d.flow('s_smokeok','s_rollback','Нет',('B','T'),label_at=(660,1000),default=True)
    d.flow('s_rollback','s_alert',sides=('B','T')); d.flow('s_alert','s_rollbackend',sides=('B','T'))
    d.note(p,'Criteria','Пример проектных критериев (не результаты измерений):\nкачество ответов ≥ 85%; не хуже текущей LLM; p95 ≤ 10 с; стоимость ≤ заданного бюджета. Ошибка или тайм-аут проверки → отказ кандидату.',(840,1290,860,140),'s_accept')
    d.note(p,'Limits','Пример лимитов: максимум 3 кандидата и 30 минут на цикл. Проверять остаток бюджета перед каждым прогоном. При недоступности текущей LLM считать оценку неудовлетворительной.',(840,1470,860,120),'s_retry')
    d.d.text((60,1680),'Смена LLM не требует переиндексации. При смене эмбеддера нужен отдельный процесс перестроения индекса.',font=d.small,fill='#4c6077')
    d.save()

if __name__=='__main__':
    hw2(); hw3()
