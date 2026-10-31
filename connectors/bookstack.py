"""Content totals and the latest page metadata visible to a BookStack token."""
import asyncio
import re


class BookStack:
    id = 'bookstack'
    name = 'BookStack'
    description = 'Books, chapters and pages, plus the three most recently updated pages visible to this token.'
    secret_label = 'Token secret'
    fields = [{'key':'token_id','label':'Token ID','placeholder':'BookStack API token ID',
               'help':'Create an API token in your BookStack user profile.'}]

    def validate(self, config):
        token_id = str(config.get('token_id', '')).strip()
        if not re.fullmatch(r'[A-Za-z0-9]{1,128}', token_id):
            raise ValueError('Enter a valid BookStack token ID')
        return {'token_id':token_id}

    async def poll(self, client, base, config, secret):
        from . import Result, fetch
        config = self.validate(config)
        headers = {'Authorization':'Token ' + config['token_id'] + ':' + secret}

        async def get(path):
            return await fetch(client, base + '/api/' + path, headers)

        books, chapters, pages = await asyncio.gather(
            get('books?count=100'), get('chapters?count=1'), get('pages?count=3&sort=-updated_at'))
        metrics = []
        for label, data in [('Books',books),('Chapters',chapters),('Pages',pages)]:
            total = data['total']
            if type(total) is not int or total < 0:
                raise ValueError('Expected content total')
            metrics.append({'label':label,'value':f'{total:,}'})
        recent = pages['data'][:3]
        names = {b['id']:b['name'] for b in books['data']}
        missing = list({p['book_id'] for p in recent} - names.keys())
        if any(type(ident) is not int or ident < 1 for ident in missing):
            raise ValueError('Invalid book ID')
        extra = await asyncio.gather(*(get(f'books/{ident}') for ident in missing))
        names.update({ident:book['name'] for ident,book in zip(missing,extra)})
        rows = [{'title':str(page['name'])[:250], 'subtitle':str(names[page['book_id']])[:250],
                 'date':str(page['updated_at'])[:10]} for page in recent]
        return Result('healthy','BookStack API available',metrics,
                      {'heading':'Recently updated pages','items':rows,'empty':'No pages available.'})
