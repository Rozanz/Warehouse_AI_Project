"""Run on a disposable database. --ai runs real Ollama + LangGraph; no live payment/shipping."""
import argparse,csv,os,tempfile,time,re,json
from pathlib import Path
import engine as e
CASES=[('What is the status of my order?','STATUS'),('Is this product in stock?','INVENTORY'),('Did my transaction go through?','PAYMENT'),('I want to cancel my order.','CANCEL'),("I changed my mind about buying these headphones. I don't need them anymore.",'CANCEL'),('I would like my money back.','REFUND'),("Cancel if it hasn't shipped, otherwise update my address.",'REVIEW')]
def main():
    p=argparse.ArgumentParser();p.add_argument('--ai',action='store_true');a=p.parse_args();out=[]
    with tempfile.TemporaryDirectory() as tmp:
        e.DB=Path(tmp)/'bench.db';e.init()
        for mode in (['0','1'] if a.ai else ['0']):
            os.environ['WAREHOUSE_AI']=mode
            for msg,expected in CASES:
                start=time.perf_counter();r=e.request('cust_402','ORD-99182',msg);ms=round((time.perf_counter()-start)*1000,2);logs=e.rows('SELECT * FROM logs WHERE trace=?',(r['trace'],));intent=next((x['detail'] for x in logs if x['node']=='Triage Agent'),'');llm=next((x['detail'] for x in logs if x['node']=='LLM classifier'),'not used');out.append(dict(mode='AI' if mode=='1' else 'Rules',message=msg,expected=expected,actual=intent,correct=intent==expected,outcome=r['outcome'],ms=ms,llm=llm));print(out[-1],flush=True)
        print('Warehouse experiment:',json.dumps([e.scale(n) for n in (50,500,50)],indent=2))
    path=Path('benchmark-results.csv');
    with path.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    for mode in set(x['mode'] for x in out):
        rows=[x for x in out if x['mode']==mode];print(mode,sum(x['correct'] for x in rows),'/',len(rows),'correct')
    print('Saved',path)
if __name__=='__main__':main()
