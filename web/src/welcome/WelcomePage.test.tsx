import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import WelcomePage from './WelcomePage';

it('shows English proof points and a direct English autoplay link without images', () => {
  const { container } = render(<WelcomePage />);
  expect(container.querySelector('section')).toHaveAttribute('lang', 'en');
  const proof = within(screen.getByRole('list', { name: 'Proof points' }));
  expect(proof.getByRole('link', { name: '1,115 backend tests in CI' })).toHaveAttribute('href', 'https://github.com/codelformat/Aftersales-agent/actions');
  expect(proof.getByRole('link', { name: '7 recorded real sessions' })).toHaveAttribute('href', '#/theater');
  expect(proof.getByRole('link', { name: 'Recall@1 0.861 · faithfulness 0.964' })).toHaveAttribute('href', 'https://github.com/codelformat/Aftersales-agent#measured-not-claimed');
  expect(screen.getByRole('link', { name: 'Watch the knowledge flywheel (27 s)' })).toHaveAttribute('href', '#/theater/flywheel?t=0&view=eng&lang=en&autoplay=1');
  expect(screen.getByRole('link', { name: 'Portfolio' })).toHaveAttribute('href', 'https://github.com/codelformat');
  expect(screen.getByRole('link', { name: 'Browse all 7 scenes' })).toHaveAttribute('href', '#/theater');
  for (const link of screen.getAllByRole('link').filter(link => link.getAttribute('href')?.startsWith('https:'))) {
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
  }
  expect(container.querySelectorAll('img')).toHaveLength(0);
});
