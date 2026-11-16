"""Durable multi-database case checkpoints; the public marker commits last."""
from .taskpack import digest


def commit_expert_batch(writer, store, completed):
    if not completed:return
    # A completed-case marker may be published only after its canonical source,
    # private reference and image bytes are durable. Public tasks and markers
    # share one transaction; no marker can outlive an uncommitted dependency.
    store.db.commit();writer.private.commit();writer.media.commit()
    writer.public.executemany('INSERT INTO completed_subjects VALUES (?)',[(ident,) for ident in completed])
    writer.public.commit();completed.clear()


def reset_incomplete_subject(writer, subject):
    # A crash after private commit but before the public marker can leave gold
    # with no public task row. Stable IDs recover those orphans as well.
    ids={row[0] for row in writer.public.execute('SELECT id FROM tasks WHERE subject=?',(subject,))}
    # Compiled candidate identities share the exact Task identity digest, even
    # when a source subject has several distinct question/date anchors.
    ids.update('a_'+row[0][:32] for row in writer.private.execute(
        "SELECT id FROM candidates WHERE subject=? AND status='compiled'",(subject,)))
    writer.private.executemany('DELETE FROM gold WHERE id=?',[(ident,) for ident in ids])
    for template,status,count in writer.private.execute('SELECT template,status,count(*) FROM candidates WHERE subject=? GROUP BY template,status',(subject,)):
        writer.counts[template+':'+status]-=count
    writer.private.execute('DELETE FROM candidates WHERE subject=?',(subject,))
    writer.public.execute('DELETE FROM tasks WHERE subject=?',(subject,))


def reset_incomplete_expert(writer, subject):
    reset_incomplete_subject(writer,subject)
    # Also tolerate legacy image-case gold written before its candidate row.
    ids=['a_'+digest((writer.dataset,template,subject,'image_case'))[:32] for template in ['D02','C03']]
    writer.private.executemany('DELETE FROM gold WHERE id=?',[(ident,) for ident in ids])
