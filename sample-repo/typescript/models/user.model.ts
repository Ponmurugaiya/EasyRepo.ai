/**
 * User data structure contract.
 */
export interface UserRecord {
  id: string;
  email: string;
  role: string;
  createdAt: Date;
}

/**
 * Model representing user entities within TypeScript codebase context.
 */
export class UserModel implements UserRecord {
  public createdAt: Date;

  /**
   * Constructs a new UserModel instance.
   * @param id Unique user identifier string
   * @param email Valid email address
   * @param role Authorization role, default is 'user'
   */
  constructor(
    public id: string,
    public email: string,
    public role: string = 'user'
  ) {
    this.createdAt = new Date();
  }

  /**
   * Serializes user attributes into a plain JSON object representation.
   */
  public toJSON(): Record<string, unknown> {
    return {
      id: this.id,
      email: this.email,
      role: this.role,
      createdAt: this.createdAt.toISOString(),
    };
  }

  /**
   * Formats user summary for log outputs.
   */
  public getFormattedDetails(): string {
    return `UserModel[${this.id}] <${this.email}> (${this.role})`;
  }
}
