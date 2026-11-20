from datetime import timedelta
from sqlalchemy import select
from .models import *
from .common import *
from .fit_model import predict

FIT='合身'

def _profile(s,sale,profile_id):
 if profile_id is not None:return get(s,FitProfile,profile_id)
 if sale.fitting_id is not None:
  f=s.get(Fitting,sale.fitting_id)
  if f:return get(s,FitProfile,f.profile_id)
 ex=s.scalars(select(Exchange).where(Exchange.replacement_sale_id==sale.id)).first()
 if ex:return get(s,FitProfile,ex.profile_id)
 fail('请选择买家体型')

def assess_fit(s,g,p):
 m=next((m for m in rows(s,Measurement) if m.garment_id==g.id),None)
 if not m:return None
 r=predict(dump(m),dump(p))
 return r,float(r['probabilities'].get(FIT,0))

def _guard(s,c,sale):
 if s.scalars(select(Exchange).where(Exchange.sale_id==sale.id)).first():fail('原单已换货，不能重复换货')
 if s.scalars(select(Return).where(Return.sale_id==sale.id)).first():fail('原单已登记退货，不能再换货')
 if sale.state=='settled':return
 if sale.state!='completed':fail('原单已退货或已换货，不能换货')
 if time(c.now)>time(sale.return_deadline):fail('已超出七天换货期')

def _sellable(s,g,c):
 if g.state!='listed':return False
 a=s.get(Agreement,g.agreement_id)
 return bool(a and a.state=='active' and time(c.now)<time(a.ends))

def candidates(s,d):
 c=get(s,Shop,1);sale=get(s,Sale,d['sale_id']);_guard(s,c,sale)
 p=_profile(s,sale,d.get('profile_id'))
 out=[]
 for g in rows(s,Garment):
  if g.id==sale.garment_id or not _sellable(s,g,c):continue
  fit=assess_fit(s,g,p)
  if not fit or fit[0]['label']!=FIT:continue
  out.append({'garment_id':g.id,'code':g.code,'title':g.title,'price_cents':g.price_cents,'fit_label':FIT,'fit_probability':round(fit[1],6),'difference_cents':g.price_cents-sale.price_cents})
 out.sort(key=lambda x:(-x['fit_probability'],x['difference_cents'],x['garment_id']))
 return {'sale_id':sale.id,'profile_id':p.id,'original_price_cents':sale.price_cents,'candidates':out}

def confirm(s,d):
 c=lock(s);sale=get(s,Sale,d['sale_id'])
 ex=s.scalars(select(Exchange).where(Exchange.sale_id==sale.id)).first()
 if ex:return ex
 _guard(s,c,sale)
 g=garment(s,d['replacement_garment_id'])
 if g.id==sale.garment_id:fail('替换款不能是原单衣物')
 if not _sellable(s,g,c):fail('替换款已被抢先买走或不可售，原单保持原样')
 p=_profile(s,sale,d.get('profile_id'))
 fit=assess_fit(s,g,p)
 if not fit:fail('替换款没有实测记录，不能判断合身')
 if fit[0]['label']!=FIT:fail('替换款未被模型判定为合身，不能用于换货')
 a=get(s,Agreement,g.agreement_id)
 price=d.get('price_cents') or g.price_cents
 if price<a.floor_cents:fail('替换款成交价低于寄售底价')
 commission=(price*a.commission_bp+5000)//10000
 now=c.now
 new_sale=Sale(fitting_id=None,garment_id=g.id,price_cents=price,commission_bp=a.commission_bp,commission_cents=commission,net_cents=price-commission,sold_at=now,return_deadline=(time(now)+timedelta(days=7)).isoformat(),state='completed')
 s.add(new_sale);s.flush();g.state='sold'
 original=garment(s,sale.garment_id)
 if original.state!='sold':fail('原单衣物状态异常，不能换货')
 original.state='inspection'
 s.add(Return(sale_id=sale.id,reason=('换货退回：'+d['reason'])[:500],state='pending'))
 settled=sale.state=='settled'
 if settled:
  oa=get(s,Agreement,original.agreement_id)
 else:
  sale.state='exchanged'
 ex=Exchange(sale_id=sale.id,profile_id=p.id,replacement_garment_id=g.id,replacement_sale_id=new_sale.id,original_price_cents=sale.price_cents,replacement_price_cents=price,difference_cents=price-sale.price_cents,fit_probability=round(fit[1],6),reason=d['reason'],state='completed',created_at=now)
 s.add(ex);s.flush()
 if settled:s.add(Clawback(exchange_id=ex.id,consignor_id=oa.consignor_id,amount_cents=sale.net_cents,remaining_cents=sale.net_cents,created_at=now))
 s.flush()
 return ex
