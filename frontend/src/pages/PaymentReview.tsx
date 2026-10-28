import { Navigate, useSearchParams } from 'react-router-dom'
export interface PaymentCapability { buying_group_id: number; name: string; supported: boolean; ready: boolean; reason: string | null; provider: string | null }
export default function PaymentReview() {
  const [params] = useSearchParams()
  return <Navigate replace to={`/payments${params.size ? `?${params}` : ''}`} />
}
