export function replayHead(html: string): string {
  const title = '<title>示例商城客服</title>';
  if (!html.includes(title)) throw new Error('Replay HTML template title has changed');
  const description = 'A LangGraph after-sales agent that cites evidence, refuses when evidence is weak, and learns from human review. Real recorded sessions.';
  return html.replace(title, `<title>Aftersales Agent · Replay</title>
    <meta name="description" content="${description}" />
    <meta property="og:description" content="${description}" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="Aftersales Agent · Replay" />
    <meta property="og:url" content="https://codelformat.github.io/Aftersales-agent/" />
    <meta property="og:image" content="https://codelformat.github.io/Aftersales-agent/og.png" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta name="twitter:card" content="summary_large_image" />`);
}
