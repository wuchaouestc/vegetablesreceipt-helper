"""Escaped, offline HTML previews. No hidden network requests or auto-corrections."""
import html
import json
from pathlib import Path
from .storage import write_json


def vocabulary_preview(records, suggestions, warnings, output):
    rows = []
    by_index = {}
    for item in suggestions:
        by_index.setdefault(item['record_index'], []).append(item)
    for index, record in enumerate(records):
        suggestion = by_index.get(index, [])
        rows.append('<tr><td>%d</td><td><pre>%s</pre></td><td><pre>%s</pre></td></tr>' %
                    (index + 1, html.escape(json.dumps(record, ensure_ascii=False, indent=2)),
                     html.escape(json.dumps(suggestion, ensure_ascii=False, indent=2))))
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>手写词典候选预览</title>
<style>body{font:16px system-ui;margin:30px;max-width:1200px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:12px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}
.note{background:#fff4d6;padding:15px}</style><h1>词典候选预览</h1><p class="note">仅供核对：原始记录完整保留，候选不会自动替换字段、删除记录或参与打印。</p>'''
    page += ''.join('<p>' + html.escape(str(w)) + '</p>' for w in warnings)
    page += '<table><tr><th>序号</th><th>原始记录</th><th>待核对候选</th></tr>' + ''.join(rows) + '</table></html>'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding='utf-8')


def annotation_preview(review, image_root, output):
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>手写素材标注草稿复核</title>
<style>body{font:16px system-ui;margin:25px}section{border-top:2px solid #ccc;padding:20px 0}
img{max-width:500px;max-height:800px;object-fit:contain}pre{white-space:pre-wrap}table{border-collapse:collapse}
td,th{border:1px solid #ccc;padding:7px}.layout{display:flex;gap:24px;flex-wrap:wrap}.note{background:#fff2cd;padding:16px}</style>
<h1>手写素材标注草稿复核</h1><p class="note">AI 视觉草稿，未获人工确认。不能作为正式准确率或冻结验收依据。点击原图可放大；逐行校对后再在 JSON 中确认。</p>'''
    for image in review['images']:
        name = image['filename']
        uri = (Path(image_root) / name).resolve().as_uri()
        page += '<section><h2>' + html.escape(name) + '</h2><p>' + html.escape(image.get('notes', '')) + '</p><div class="layout">'
        page += '<a href="' + html.escape(uri, quote=True) + '"><img src="' + html.escape(uri, quote=True) + '"></a><div>'
        page += '<p>状态：' + html.escape(image.get('review_status', 'pending')) + '</p>'
        page += '<pre>' + html.escape(json.dumps(image.get('records', []), ensure_ascii=False, indent=2)) + '</pre></div></div></section>'
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(page + '</html>', encoding='utf-8')
