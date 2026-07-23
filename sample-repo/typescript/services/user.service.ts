import { Repository as IRepository } from '../interfaces/repository.interface';
import { UserModel } from '../models/user.model';

/**
 * Service implementation handling user entity management and persistence.
 */
export class UserService implements IRepository<UserModel> {
  private memoryStorage: Map<string, UserModel> = new Map();

  /**
   * Retrieves a UserModel by ID from in-memory storage map.
   * @param id User identifier
   */
  async findById(id: string): Promise<UserModel | null> {
    return this.memoryStorage.get(id) || null;
  }

  /**
   * Saves or updates a UserModel instance in storage map.
   * @param entity UserModel instance to save
   */
  async save(entity: UserModel): Promise<boolean> {
    this.memoryStorage.set(entity.id, entity);
    return true;
  }

  /**
   * Removes a UserModel entry matching the specified ID.
   * @param id User identifier
   */
  async delete(id: string): Promise<boolean> {
    return this.memoryStorage.delete(id);
  }
}
