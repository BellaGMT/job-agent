/**
 * Run `worker` over `items` with at most `size` in flight.
 *
 * Lanes pull from one shared iterator, so each item is processed exactly once.
 * On the first failure, lanes stop taking new items, in-flight work finishes,
 * and the first error is rethrown: nothing keeps running in the background
 * after this function has rejected.
 */
export async function runPool<T>(items: Iterable<T>, size: number, worker: (item: T) => Promise<void>): Promise<void> {
  if (!(size >= 1)) throw new Error(`Pool size must be at least 1, got ${size}`);
  const iterator = items[Symbol.iterator]();
  let failure: { error: unknown } | undefined;

  const lane = async (): Promise<void> => {
    for (let step = iterator.next(); !step.done && !failure; step = iterator.next()) {
      try {
        await worker(step.value);
      } catch (error) {
        failure ??= { error };
      }
    }
  };
  await Promise.all(Array.from({ length: size }, lane));
  if (failure) throw failure.error;
}
