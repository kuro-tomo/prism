import ProfileClient from "./ProfileClient"

export const metadata = { title: "PRISM — 会社プロフィール" }

interface Props {
  searchParams: Promise<{ first?: string }>
}

export default async function ProfilePage({ searchParams }: Props) {
  const params = await searchParams
  return <ProfileClient isFirst={params.first === "1"} />
}
