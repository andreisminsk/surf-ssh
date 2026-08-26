import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface Props {
  content: string;
  host: string;
  remotePath: string;
}

export function MarkdownViewer({ content, host, remotePath }: Props) {
  const dirPath = remotePath.substring(0, remotePath.lastIndexOf('/'));
  const basePath = `/api/v1/hosts/${host}/file?path=${encodeURIComponent(dirPath)}/`;

  return (
    <div className="viewer-container">
      <base href={basePath} />
      <div className="markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code({ inline, className, children, ...props }: any) {
              const match = /language-(\w+)/.exec(className || '');
              return !inline && match ? (
                <SyntaxHighlighter
                  style={oneDark}
                  language={match[1]}
                  PreTag="div"
                  {...props}
                >
                  {String(children).replace(/\n$/, '')}
                </SyntaxHighlighter>
              ) : (
                <code className={className} {...props}>{children}</code>
              );
            },
            img({ src, alt, ...props }: any) {
              const imgSrc = src?.startsWith('http') ? src : `${basePath}${src}`;
              return <img src={imgSrc} alt={alt} {...props} />;
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    </div>
  );
}
