import {test,expect} from './fixtures'

test('Manual payment creation and source-field mutation are rejected',async ({app,data})=>{
  const created=await data.request.post('/api/payments',{headers:data.headers,data:{buying_group_id:1}})
  expect(created.status()).toBe(405)
  const updated=await data.request.patch('/api/payments/99999999',{headers:data.headers,data:{payment_received_at:'2026-10-27T12:00:00Z'}})
  expect(updated.status()).toBe(409)
  const deleted=await data.request.delete('/api/payments/99999999',{headers:data.headers})
  expect(deleted.status()).toBe(409)
  await app.goto('/payments')
  await expect(app.getByRole('heading',{name:'Payments',exact:true})).toBeVisible()
  await expect(app.getByRole('button',{name:'Add payment',exact:true})).toHaveCount(0)
  await expect(app.getByRole('button',{name:'Mark received',exact:true})).toHaveCount(0)
})
