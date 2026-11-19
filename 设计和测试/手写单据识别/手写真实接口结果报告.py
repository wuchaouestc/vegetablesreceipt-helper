"""Generate live findings and human review form from saved responses, no network."""
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from handwriting.storage import read_json, write_json, fingerprint
from handwriting.evaluation import region_text


def main():
    output=ROOT/'data/evaluations/stage012_20261106'
    results=read_json(output/'results.json')
    visual=read_json(ROOT/'data/annotations/visual_review.json')
    groups=defaultdict(list)
    keyed={}
    for r in results:
        provider=r['request']['provider']; name=provider.get('action') or provider.get('model')
        groups[name].append(r)
        keyed[(r['request']['image_sha256'],name)]=r['response']
    summary={}
    for name,items in groups.items():
        timings=[x['response']['elapsed_seconds'] for x in items]
        summary[name]={'requests':len(items),'ok':sum(x['response']['status']=='ok' for x in items),
            'errors':[x['response']['error_code'] for x in items if x['response']['status']=='error'],
            'median_seconds':round(statistics.median(timings),3),'min_seconds':min(timings),'max_seconds':max(timings),
            'text_blocks':sum(len(x['response'].get('blocks',[])) for x in items),
            'records': [len(x['response'].get('records',[])) for x in items],
            'usage':{k:sum(x['response'].get('usage',{}).get(k,0) for x in items) for k in ['prompt_tokens','completion_tokens','total_tokens']}}
    images=[]
    for annotation in visual['images']:
        sha=annotation['sha256']
        if not annotation.get('records') or (sha,'deepseek-chat') not in keyed:continue
        rows=[]
        for row in annotation['records']:
            rows.append({'row_id':row['row_id'],
                'accurate':region_text(keyed[(sha,'GeneralAccurateOCR')]['blocks'],row['bbox']),
                'handwriting':region_text(keyed[(sha,'GeneralHandwritingOCR')]['blocks'],row['bbox'])})
        images.append({'image_sha256':sha,'image_path':annotation['filename'],
            'uri':(ROOT/'data/手写素材'/annotation['filename']).resolve().as_uri(),
            'records':[{**r,'prediction_index':None} for r in annotation['records']],
            'predictions':keyed[(sha,'deepseek-chat')]['records'],
            'model_notes':keyed[(sha,'deepseek-chat')]['notes'],'ocr_rows':rows,'complete':False})
    payload={'results_fingerprint':fingerprint(results),'images':images}
    raw=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=(ROOT/'handwriting/human_review.html').read_text(encoding='utf-8')
    (output/'业务人工复核.html').write_text(template.replace('__PAYLOAD__',raw),encoding='utf-8')
    write_json(output/'live_findings.json',{'groups':summary,'requests':len(results),'human_confirmed_accuracy':None,
        'remaining_calls':30-len(read_json(output/'request_ledger.json')),
        'notes':['OCR成功是接收测试，不等于字段正确。','reasoner在4096输出token限制下截断，不等于生产无限制请求必然失败。',
                 '人工确认未完成，禁止将视觉草稿记作业务准确率。']})
    status=read_json(output/'stage_status.json')
    status['stage0']['live_capability']='executed_see_live_findings'
    status['stage0']['acceptance']='pending_business_human_confirmation'
    status['stage2']['real_results']='29_requests_completed_no_accuracy_claim'
    status['real_api_calls_performed']=len(results)
    write_json(output/'stage_status.json',status)
    lines=['# 手写单据真实接口测试结果','', '日期：2026-11-06。图片增强关闭，原图字节不改；生产配置与代码未改。', '',
           '## 接收与输出测试','', '| 接口 | 请求数 | 有效结果 | 耗时中位数 | 最慢请求 |', '| --- | ---: | ---: | ---: | ---: |']
    for name,s in summary.items():lines.append(f"| {name} | {s['requests']} | {s['ok']} | {s['median_seconds']} 秒 | {s['max_seconds']} 秒 |")
    lines += ['', '累计29次请求，30次预算剩余1次；没有自动重试。', '',
        '- 两种腾讯接口各对13张图成功返回OCR文字。返回文字块数量不同不能用作准确率高低结论。',
        '- deepseek-chat 对2张有草稿标注的整图返回结构化明细；确切条数见下表。',
        '- deepseek-reasoner 请求到达并返回token用量，但达到4096输出token上限而被适配器判为 incomplete_response，没有可用完整明细。不是鉴权失败或拒收图片，也不能据此断言原软件不设此上限时必然失败。', '',
        '| 标注样本 | 草稿行数 | 模型明细数 | 人工确认 |','| --- | ---: | ---: | --- |']
    for image in images:lines.append(f"| {image['image_path']} | {len(image['records'])} | {len(image['predictions'])} | 待完成 |")
    lines += ['', '## 效果与准确率边界','',
        '已检查第一张原图及对应真实输出，重点差异如下（待业务确认，不计为人工准确率）：', '',
        '| 位置/编码 | 高精度OCR | 旧手写OCR | deepseek-chat |',
        '| --- | --- | --- | --- |',
        '| 第一张左上栏，1369的数量 | 45斤 | 45斤 | 4.5斤 |',
        '| 第一张中下部，185的数量 | 0.5斤 | 0.49 | 2.5斤 |', '',
        '原图视觉上分别接近4.5和0.5，说明小数点、手写单位和数字仍有分歧；模型可能纠正某处OCR，也可能在另一处自行误读。不能因为两次OCR相同或模型返回JSON就认定正确，也没有证据支持直接切换旧手写接口。', '',
        '已生成“业务人工复核.html”，包含原图、40行可编辑草稿、真实模型记录以及同一区域的两种OCR文字。须先核对标准答案，再按原图位置选择对应模型行；不能按数量相同自动匹配。',
        '当前已确认标准答案为0，因此业务人工确认后的准确率暂不可计算，字段准确率、整行正确率不能填成0%或100%。两种OCR仅有独立原始文字，不把分块方式或草稿字面相符度称为业务明细正确率。',
        '人工导出“手写业务人工确认.json”后运行：', '', '```powershell',
        '.\\.venv\\Scripts\\python.exe 设计和测试/手写人工确认评分.py "人工确认文件的完整路径"', '```','',
        '评分工具验证原图哈希、实测结果指纹、人工确认标志、完整性和一对一位置映射。评分范围为已确认样本的 deepseek-chat 原始明细，不冒充全13图、生产合并流程或泛化准确率。原始草稿不自动覆盖，确认文件单独留存。', '',
        '## 用量与文件','', '价格及账户优惠未核验，本报告只列实际token与调用次数，不虚构人民币费用。']
    for name,s in summary.items():
        if s['usage']['total_tokens']:lines.append(f"- {name}：输入{s['usage']['prompt_tokens']}，输出{s['usage']['completion_tokens']}，合计{s['usage']['total_tokens']} tokens。")
    lines += ['', '结果目录：`data/evaluations/stage012_20261106/`。`results.json`、`request_ledger.json`、`live_findings.json` 保留响应、请求ID、调用计数、错误与耗时；未在报告打印密钥。',
              '这份报告更新此前“未执行真实接口”的状态；以前离线报告保留为历史记录。正式发布仍不切换OCR或模型，等待效果证据。']
    (ROOT/'设计和测试/手写真实接口测试报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
