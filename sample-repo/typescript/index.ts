import { UserService as UserServiceImpl } from './services/user.service';
import { UserModel } from './models/user.model';

/**
 * Main application execution routine for TypeScript synthetic test module.
 */
async function main(): Promise<void> {
  const userService = new UserServiceImpl();
  const newUser = new UserModel('ts_usr_1001', 'dev@example.ts', 'developer');

  console.log('Saving TS User:', newUser.getFormattedDetails());
  await userService.save(newUser);

  const retrievedUser = await userService.findById('ts_usr_1001');
  if (retrievedUser) {
    console.log('Successfully retrieved user payload:', retrievedUser.toJSON());
  } else {
    console.error('Failed to retrieve TS user');
  }
}

main().catch(console.error);
