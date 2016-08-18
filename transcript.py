#!/usr/bin/env python3
#-*-coding:utf-8-*- 
"""
Get semantic HTML from PDFs converted by pdf2htmlEX.

- Reconstruct mark-up based on visual conventions:
  * paragraphs
  * headings
  * lists
  * tables
  
- Allows to remove repetitive headers (and footers) from each page
  based on common pattern repeated accross topmost elements
  producing a continuous as opposed to a paged document.

- Allows to remove repetitive passages
- Reduces code
- Batch processing
  pdf2html.py is the 1st step of the batch process
- Configurable
  See config.py for options

"""
from lxml.html import Element, fromstring, tostring
from ttf import pua_content, recover_text
import collections
import types
import re
import glob
import os.path
import sys
import pynliner
from pynliner import Pynliner
import chardet

DEBUG = 0
MIN_SPAN_SIZE = 8 # remove spans less then this width (in px)
MAX_LINE_HEIGHT = 18 # lines over this height indicate new paragraphs
TABLES = 1 # reconstruct tables
STRIP_CSS = 1 # remove styles when done to show off the naked semantic HTML
              # and get ready for custom CSS, also removes paging data attributes
BR = 1 # place break rules at original line endings
REMOVE_HEADERS = 1
BULLETS = ('•', '○', '■') # list item bullets
REMOVE_BEFORE = (
    r'<span class="_ _[a-f0-9]{1,2}"></span>', # empty spans
    r'<script.*?</script>(?s)', # scripts
    r'<link.*?\.css"/>', # css file links
    r'<meta (name|http-equiv).*?>', # meta tags
    r'<!--.*?-->', # html comments
    r'<img alt="" src="pdf2htmlEX-64x64.png"/>',
    r'<a class=".*?</a>'
)
REMOVE_AFTER = (
    '<table></table>',
    '<title></title>',
    '<span>', '</span>',
    '<div>', '</div>'
)
REPLACE_AFTER = ()
HTML_DIR  = './'

try:
    import config
    REMOVE_BEFORE += config.REMOVE_BEFORE
    REPLACE_AFTER += config.REPLACE_AFTER
    BULLETS       += config.BULLETS
    HTML_DIR  = config.HTML_DIR
except ImportError:
    print("config.py not found. Using default configuration.")

CSS_CLASS_MAP = { # pdf2htmlEX convention for CSS class names and corresponding properties
    '_' : 'display:.*?',
    'm' : 'transform',
    'w' : 'width',
    'h' : 'height',
    'x' : 'left',
    'y' : 'bottom',
    'ff': 'font-family',
    'fs': 'font-size',
    'fc': 'color',
    'sc': 'text-shadow',
    'ls': 'letter-spacing',
    'ws': 'word-spacing'
}
# DOM element utilities
parent = lambda e: e.getparent()
exists = lambda e: e is not None
remove = lambda e: parent(e).remove(e)
# insert element just after another one
insert = lambda e, a: parent(a).insert(parent(a).index(a)+1, e)
# cut CSS class hex number out of HTML element's class attribute
classN = lambda class_, el: el.attrib['class'].split(' ' + class_)[1].split()[0]

def css_sizes(class_, css):
    """Scan CSS for specific rules and return sorted class numbers and sizes.
    """
    property_ = CSS_CLASS_MAP[class_]
    px_value = '(\d{1,3})(?:\.\d+)?px'
    hex_id = '([a-f0-9]{1,3})'
    rule = r'\.%s%s{%s:%s;}' % (class_, hex_id, property_, px_value)
    sizes = collections.OrderedDict()
    for hex_, px_ in re.findall(rule, css):
        sizes[hex_] = int(px_)
    return sizes

def wrap_set(dom, child_tag, parent_tag):
    """Wrap unbroken sets of elements in a parent container:
        - <li> in a <ul>
        - <tr> in a <table>
    """
    nxt = 0
    for e in dom.cssselect(child_tag):
        if nxt != e:
            box = Element(parent_tag)
            insert(box, e)
        box.append(e)
        nxt = parent(e).getnext()
        if nxt is None:
            nxt = e.getnext()

def remove_headers(dom):
    leading = [] # collect topmost elements on each page and their joined text
    for n1 in dom.cssselect('.pc > *:first-child'): # for each 1st element on page
        n1_y = classN('y', n1) # get its top position
        topmost = parent(n1).cssselect('.y'+n1_y) # select those with the same top position
        header_txt = ' '.join([x.text_content() for x in topmost])
        # strip all numbers (including page numbers)
        header_txt = ''.join([a for a in header_txt if not a.isdigit()]).strip()
        # if the same text is repeated on top of every page, that's headers
        leading.append((topmost, header_txt))
    texts = [txt for topmost,txt in leading if txt] # collect non-empty texts
    if len(texts) > 1 and all(x == texts[-1] for x in texts[1:]): # if they are all the same from 2nd page
        for topmost,txt in leading:
            if texts[-1] in txt: # keep empty topmost
                if DEBUG: print("Removing header:", txt)
                for each in topmost: remove(each)
    return dom

def grid_data(dom, get_dimension):
    data = []
    for l in dom.cssselect('.t'):
        # get page number of the current page
        page = 0
        for anc in l.iterancestors():
            if anc.attrib.get('class', '').startswith('pc '):
                page = int(classN('pc', anc), 16)
                break
        # collect elements and their coordinates for ordering
        # if text box (.t) has a parent clip box (.c) this affects actual coordinates
        cb = parent(l) if parent(l).attrib.get('class', '').startswith('c ') else None
        
        # collect data enriched with actual x and y coords
        x = get_dimension(l, 'x') # left
        y = get_dimension(l, 'y') + get_dimension(l, 'h') # bottom
        
        if exists(cb): # adjust for the clip box coordinates
            x = get_dimension(cb, 'x') + x
            y = get_dimension(cb, 'y')
        
        paper_height = 850 # height of A4 page in px
        y = paper_height - y # turn bottom position into top
        data.append(
            types.SimpleNamespace(page=page, x=x, y=y, elem=l, clipbox=cb, lines=[], text=l.text)
        )
    return data

def reconstruct_tables(dom, data):
    # order data vertically into row lists by page, row and finally column
    rows = collections.OrderedDict()
    cboxes = {}
    for c in sorted(data, key=lambda c: (c.page, c.y, c.x)):
        # combine page number and row position to get a useful key
        key = '%d,%d' % (c.page, c.y)
        # create row lists(y) and clipbox groups(x)
        rows.setdefault(key,         []).append(c)
        cboxes.setdefault(c.clipbox, []).append(c.elem)
    
    from pprint import pprint
    # pprint(rows)
    
    # collect cell lines with same clip boxes
    merged = []
    for key, row in rows.items():
        for cell in row:
            if cell.clipbox in merged:
                rows[key] = [c for c in rows[key] if c != cell]
            else:
                cell.lines = cboxes[cell.clipbox]
                merged.append(cell.clipbox)
    
    for row in rows.values():
        # hardly a table row if there is only one non-empty element in it at the start of a line
        if len([c for c in row if c.text]) > 1:
            tr = parent(row[0].elem)
            tr.tag = 'tr'
            for cell in row:
                cell.elem.tag = 'td'
                cell.elem.attrib['class'] = ''
                for line in cell.lines[1:]:
                    line.attrib['class'] = ''
                    if BR: cell.elem.append(Element('br'))
                    cell.elem.append(line)
                tr.append(cell.elem)
    # drop empty span, divs
    for e in dom.iter():
        if e.tag in ('span','div') and not e.text_content() or e.text_content() == ' ':
            e.drop_tag()
    
    wrap_set(dom, 'tr', 'table')
    return dom

def prepare(doc_path):
    doc = s = open(doc_path,'rt',encoding='utf-8').read()
    css =     open(doc_path.replace('.html', '.css'),'rt',encoding='utf-8').read()
    
    for rm in REMOVE_BEFORE:
        s = re.sub(rm, '', s)
    
    # round pixel sizes to whole pixels
    for no in re.findall(r'(\d{1,3}\.\d{6})px;', css):
        css = css.replace(no, str(int(round(float(no)))))
    
    dimensions = {x: css_sizes(x, css) for x in '_ fs h x y'.split()}
    
    # remove spacing spans of very small width
    span_sizes = dimensions['_'].items()
    for no, size in span_sizes:
        if int(size) < MIN_SPAN_SIZE:
            span = '<span class="_ _%s"> </span>' % no
            s = s.replace(span, '')
    
    dom = fromstring(s)
    return dom, dimensions

def heading_levels(dom, dimensions):
    # find most common font-size(fs), font-sizes bigger than that are headings
    fs_stats = [(len(dom.cssselect('.fs'+cssn)), cssn, fs) for cssn, fs in dimensions['fs'].items()]
    top_stats = sorted(fs_stats, key=lambda x: x[0], reverse=True)
    prevalent_fs = top_stats[0][-1]
    headings = [x for x in reversed(fs_stats) if x[-1] > prevalent_fs]
    # match font-size classes to heading levels
    h_levels = {}; level = 1
    for count, cssn, size in headings:
        h_levels[cssn] = level
        level += 1
    return h_levels

def semanticize(doc_path='test.html'):
    """
    P: unbroken set of lines (.t divs) of the same look make one <p>
    H1-3: Top 3 kinds of font size are turned to h1, h2 and h3.
    TABLE: use x and y position to indicate <td>, TODO: colspan support
    """
    print(doc_path)
    dom, dimensions = prepare(doc_path)
    get_dimension = lambda el, dim_type: dimensions[dim_type].get(classN(dim_type, el)) or 0
    
    # recover text from embedded fonts with bad CMAPS if > 50% of characters are unicode PUA
    recover = pua_content(dom.text_content()) > 0.5
    if recover:
        print('Recovery needed, not now.')
        return
        recover_text(dom, os.path.dirname(doc_path))
    
    # remove paging headers
    if REMOVE_HEADERS:
        dom = remove_headers(dom)
    
    # remove javascript holders
    for div in dom.cssselect('.j'):
        remove(div)
    
    if TABLES:
        table_data = grid_data(dom, get_dimension)
        dom = reconstruct_tables(dom, table_data)
    
    h_levels = heading_levels(dom, dimensions)
    
    # line by line analysis and conversion
    p_look = p_height = p_space = p_tag = box = 0
    
    for l in dom.cssselect('.t'):
        # Gather information about this line to see if it's part of a block.
        # 1. detect change of look - different css classes from previous line
        look = ' '.join([c for c in l.attrib['class'].split() if c[0]!='y' and c[0:2]!='fc']) # ignore y pos and font color
        new_look = p_look != look
        # 2. detect change of margin height - larger difference in bottom position from previous line
        height = get_dimension(l, 'h')
        line_height = p_height - height
        margin = line_height > MAX_LINE_HEIGHT
        # 3. space above - preceding empty line
        space = not l.text_content().strip()
        
        # Based on collected info: does this line belong to previous line?
        append = new_look == p_space == margin == False
        
        txt = l.text_content()
        
        tag = 'p'
        
        # LI
        indent = 'x0' not in look # there is some indentation
        if [1 for b in BULLETS if txt.startswith(b)]:
            tag = 'li'
            append = 0
        elif indent and p_tag == 'li':
            tag = 'li'
            append = 1
        # H1, H2...
        size = classN('fs', l)
        if size in h_levels.keys():
            append = 0
            tag = 'h%s'% h_levels[size]
        
        # merge multiline-elements
        if txt.strip():
            if append:
                if BR: box.append(Element('br'))
                box.append(l)
            else:
                box = l
                l.tag = tag
        else:
            remove(l)
        
        if DEBUG:
            mark = ('<%s>' % tag).ljust(5)
            if append: mark = 5*' '
            print(' Aa %d    ⇪ %d    ⇕ % 3d    %s    %s    %s' %\
                (new_look, p_space, line_height, l.attrib['class'].ljust(40), mark, txt))
        
        # save current values for comparison in the next loop iteration
        p_space, p_height, p_look, p_tag = space, height, look, tag
    
    wrap_set(dom, 'li', 'ul')
    
    if STRIP_CSS:
        for e in dom.cssselect("style"):
            remove(e)
        for attr in 'style id class data-page-no data-data'.split():
            for e in dom.cssselect("*"):
                try: del e.attrib[attr]
                except KeyError: pass
    
    # save file
    html = tostring(dom, encoding='UTF-8', pretty_print=True).decode('UTF-8')
    s = '<!DOCTYPE html>' + html
    for a,b in REPLACE_AFTER:
        s = re.sub(a, b, s)
    for rm in REMOVE_AFTER:
        s = re.sub(rm, '', s)
    for b in BULLETS: 
        s = s.replace(b, '')
    if recover:
        for rm in REMOVE_BEFORE:
            s = re.sub(rm, '', s)
    save_path = os.path.dirname(doc_path.replace('HTML', 'HTM')) + '.htm'
    f = open(save_path, 'w', encoding='UTF-8')
    f.write(s)
    f.close()

def css_inline_html(doc_path):
    f_contents = open(doc_path,'rb').read()
    p = Pynliner()
    inline_html = p.from_string(f_contents).run()
    inline_html_str = inline_html.encode('unicode_escape')
    #这时候成了ascii 例如    \\u9001\\u68c0\\u65e5\\u671f\\uff1a
    inline_html_str1 = inline_html_str.decode("utf8")
    #  \u9646\u6653\u65ed
    save_path = os.path.dirname(doc_path.replace('HTML', 'HTML')) + '-1.html'
    f = open(save_path, 'w', encoding='UTF-8')
    # https://segmentfault.com/q/1010000000344967
    inline_html_str2 = inline_html_str1.encode('utf-8').decode("unicode_escape")
    f.write(inline_html_str2)
    f.close()

def batch_process(docs, limit=None):
    for i, path in enumerate(glob.glob(docs)):
        if i==limit: break
        try:
            css_inline_html(path)
           # semanticize(path)
        except Exception as e:
            print(e)
            import traceback
            print(traceback.format_exc())
            continue

if __name__ == '__main__':
    os.makedirs(HTML_DIR.replace('HTML', 'HTM'), exist_ok=True)
    batch_process(HTML_DIR + '/*/*.html', limit=None)
