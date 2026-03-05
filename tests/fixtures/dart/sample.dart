/// User service for managing users.
class UserService {
  /// Get user by ID.
  String getUser(int userId) {
    return 'user-$userId';
  }

  /// Delete a user.
  bool deleteUser(int userId) {
    return true;
  }

  /// Whether the service is ready.
  bool get isReady => true;
}

/// Scrollable behavior.
mixin Scrollable on UserService {
  /// Scroll to offset.
  void scrollTo(double offset) {}
}

/// Authenticate a token.
bool authenticate(String token) {
  return token.isNotEmpty;
}

/// Status of a request.
enum Status { pending, active, done }

/// Helpers for String manipulation.
extension StringExt on String {
  /// Whether the string is blank.
  bool get isBlank => trim().isEmpty;
}

/// JSON map alias.
typedef JsonMap = Map<String, dynamic>;
