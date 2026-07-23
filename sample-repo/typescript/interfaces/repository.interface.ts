/**
 * Generic repository interface defining asynchronous persistence operations.
 */
export interface Repository<T> {
  /**
   * Find record by unique entity identifier.
   * @param id Entity identifier
   */
  findById(id: string): Promise<T | null>;

  /**
   * Persist entity instance to storage.
   * @param entity Entity instance
   */
  save(entity: T): Promise<boolean>;

  /**
   * Delete entity record matching given ID.
   * @param id Entity identifier
   */
  delete(id: string): Promise<boolean>;
}
