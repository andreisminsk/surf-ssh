interface Props {
  content: string;
}

export function HtmlViewer({ content }: Props) {
  return (
    <div className="iframe-container">
      <iframe
        sandbox=""
        srcDoc={content}
        title="HTML Preview"
      />
    </div>
  );
}
