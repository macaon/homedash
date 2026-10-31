import asyncio
import unittest
import httpx
from connectors import REGISTRY


class BookStackTests(unittest.TestCase):
    def poll(self, empty=False, missing=False, fail=False, bad_total=False):
        self.paths=[]
        def handler(request):
            self.assertEqual(request.method,'GET')
            self.assertEqual(request.headers['Authorization'],'Token fixtureID:fixture-secret')
            self.assertNotIn('fixture-secret',str(request.url))
            path=request.url.path
            self.paths.append(path)
            if fail:return httpx.Response(403)
            if path=='/api/books':data={'total':6,'data':[] if missing else [{'id':3,'name':'Homelab'}]}
            elif path=='/api/chapters':data={'total':'14' if bad_total else 14,'data':[]}
            elif path=='/api/pages':
                self.assertEqual(request.url.params['sort'],'-updated_at')
                self.assertEqual(request.url.params['count'],'3')
                data={'total':0 if empty else 61,'data':[] if empty else [{'name':'A page','book_id':3,'updated_at':'2026-10-31T16:02:36Z'}]}
            elif path=='/api/books/3':data={'id':3,'name':'Homelab'}
            else:self.fail('Unexpected endpoint '+path)
            return httpx.Response(200,json=data)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await REGISTRY['bookstack'].poll(client,'https://books.example',{'token_id':'fixtureID'},'fixture-secret')
        return asyncio.run(run())

    def test_totals_and_recent_metadata(self):
        result=self.poll()
        self.assertEqual([m['value'] for m in result.metrics],['6','14','61'])
        self.assertEqual(result.details['items'],[{'title':'A page','subtitle':'Homelab','date':'2026-10-31'}])
        self.assertNotIn('fixture-secret',str(result))
        self.assertFalse(any('/pages/' in p for p in self.paths))

    def test_book_name_outside_first_page_is_resolved(self):
        result=self.poll(missing=True)
        self.assertIn('/api/books/3',self.paths)
        self.assertEqual(result.details['items'][0]['subtitle'],'Homelab')

    def test_empty_pages(self):
        result=self.poll(empty=True)
        self.assertEqual(result.details['items'],[])
        self.assertEqual(result.details['empty'],'No pages available.')

    def test_permission_error_does_not_become_zero_counts(self):
        with self.assertRaises(httpx.HTTPStatusError):self.poll(fail=True)

    def test_invalid_total_is_rejected(self):
        with self.assertRaises(ValueError):self.poll(bad_total=True)

    def test_token_id_validation(self):
        for value in ('', 'id:secret', 'id\nInjected', 'a'*129):
            with self.assertRaises(ValueError):REGISTRY['bookstack'].validate({'token_id':value})
