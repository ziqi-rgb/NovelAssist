"""
清理 NovelAssist 数据库中大纲表的重复数据
用法: python cleanup_outline_dupes.py
       python cleanup_outline_dupes.py --dry-run   (仅预览，不执行)
"""
import sqlite3, sys, io, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(__file__), '.data', 'novel.db')
DRY_RUN = '--dry-run' in sys.argv

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 找出所有重复：同一 novel_id 下相同 category + title 的多条记录
cur.execute('''
    SELECT novel_id, category, title, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
    FROM outlines
    GROUP BY novel_id, category, title
    HAVING cnt > 1
    ORDER BY novel_id, category, MIN(id)
''')
dupes = cur.fetchall()

if not dupes:
    print('✅ 没有发现重复的大纲条目。')
    conn.close()
    sys.exit(0)

total_deleted = 0
for row in dupes:
    ids = [int(x) for x in row['ids'].split(',')]
    ids.sort()
    keep_id = ids[0]          # 保留最早的
    delete_ids = ids[1:]      # 删除其余
    
    # 获取保留条目的描述
    cur.execute('SELECT description FROM outlines WHERE id=?', (keep_id,))
    keep_desc = cur.fetchone()['description'] or ''
    
    # 如果保留条目描述为空，尝试从重复条目中合并
    if not keep_desc.strip():
        for did in delete_ids:
            cur.execute('SELECT description FROM outlines WHERE id=?', (did,))
            d = cur.fetchone()['description'] or ''
            if d.strip():
                keep_desc = d
                break
        if keep_desc.strip():
            print(f'  [合并描述] id={keep_id} 从重复条目中获取描述')
    
    for did in delete_ids:
        total_deleted += 1
        action = 'DRY-RUN' if DRY_RUN else 'DELETE'
        cat = row['category'] or '?'
        title = row['title'] or '?'
        nid = row['novel_id']
        print(f'  [{action}] novel={nid} [{cat}] "{title}"  id={did} (保留 id={keep_id})')
        if not DRY_RUN:
            cur.execute('DELETE FROM outlines WHERE id=?', (did,))
    
    # 如果保留条目描述有更新，写回
    if keep_desc.strip() and not DRY_RUN:
        cur.execute('UPDATE outlines SET description=? WHERE id=?', (keep_desc, keep_id))

if DRY_RUN:
    print(f'\n🔍 预览完成：将删除 {total_deleted} 条重复记录')
    print('   使用 python cleanup_outline_dupes.py 执行实际清理')
else:
    conn.commit()
    print(f'\n✅ 清理完成：已删除 {total_deleted} 条重复记录，保留最早创建的条目')
    print('   建议重启应用以刷新缓存。')

conn.close()
