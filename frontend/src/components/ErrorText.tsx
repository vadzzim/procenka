export function ErrorText({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p className="error" role="alert">
      {message}
    </p>
  );
}
