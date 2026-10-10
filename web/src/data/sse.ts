export async function* parseSse(stream: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let line = '';
  let skipLf = false;
  let event = '';
  let data: string[] = [];
  let completed = false;

  function finishLine() {
    const current = line;
    line = '';
    if (current === '') {
      const result = data.length ? { event: event || 'message', data: data.join('\n') } : undefined;
      event = '';
      data = [];
      return result;
    }
    if (current.startsWith(':')) return;
    const colon = current.indexOf(':');
    const field = colon < 0 ? current : current.slice(0, colon);
    let value = colon < 0 ? '' : current.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    if (field === 'data') data.push(value);
  }

  try {
    while (true) {
      const chunk = await reader.read();
      const text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
      for (const char of text) {
        if (skipLf) {
          skipLf = false;
          if (char === '\n') continue;
        }
        if (char === '\n' || char === '\r') {
          skipLf = char === '\r';
          const result = finishLine();
          if (result) yield result;
        } else {
          line += char;
        }
      }
      if (chunk.done) {
        completed = true;
        break;
      }
    }
  } finally {
    try {
      if (!completed) await reader.cancel();
    } catch {
      // Preserve the original stream error if cancellation also fails.
    } finally {
      reader.releaseLock();
    }
  }
}
