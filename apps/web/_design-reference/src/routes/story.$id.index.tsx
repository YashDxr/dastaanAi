import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/story/$id/")({
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/story/$id/player", params: { id: params.id } });
  },
});
