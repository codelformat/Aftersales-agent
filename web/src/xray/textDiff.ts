/** 返回新句子的字符级 LCS 差异；删除的字符只在原句中显示。 */
export function textDiff(a: string, b: string): { text: string; added: boolean }[] {
  const original = Array.from(a);
  const revised = Array.from(b);
  const lcs = Array.from({ length: original.length + 1 }, () => new Uint32Array(revised.length + 1));
  for (let i = original.length - 1; i >= 0; i--) {
    for (let j = revised.length - 1; j >= 0; j--) {
      lcs[i][j] = original[i] === revised[j] ? 1 + lcs[i + 1][j + 1] : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const parts: { text: string; added: boolean }[] = [];
  function append(text: string, added: boolean) {
    const last = parts.at(-1);
    if (last?.added === added) last.text += text;
    else parts.push({ text, added });
  }
  let i = 0;
  let j = 0;
  while (j < revised.length) {
    if (i < original.length && original[i] === revised[j]) { append(revised[j++], false); i++; }
    else if (i < original.length && lcs[i + 1][j] >= lcs[i][j + 1]) i++;
    else append(revised[j++], true);
  }
  return parts;
}
