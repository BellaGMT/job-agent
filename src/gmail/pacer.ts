/**
 * Spaces out request starts across all workers sharing it. Halves its rate on
 * every quota error (`slowDown`), so a sync settles under whatever the
 * project's real limit turns out to be.
 */
export class Pacer {
  static readonly MIN_PER_SECOND = 0.5;
  private nextAt = 0;
  private rate: number;

  constructor(
    perSecond: number,
    private readonly now: () => number = Date.now,
    private readonly sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  ) {
    if (!(perSecond > 0)) throw new Error(`Rate must be positive, got ${perSecond}`);
    this.rate = perSecond;
  }

  get perSecond(): number {
    return this.rate;
  }

  slowDown(): void {
    this.rate = Math.max(Pacer.MIN_PER_SECOND, this.rate / 2);
  }

  /** Wait until this caller's turn. Turns are reserved synchronously, so concurrent callers never share a slot. */
  async next(): Promise<void> {
    const now = this.now();
    const at = Math.max(now, this.nextAt);
    this.nextAt = at + 1000 / this.rate;
    if (at > now) await this.sleep(at - now);
  }
}
