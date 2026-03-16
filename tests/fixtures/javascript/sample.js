const MAX_TIMEOUT = 5000;

/**
 * User service class
 */
class UserService {
    getUser(userId) {
        return { id: userId };
    }
}

function authenticate(token) {
    return token.length > 0;
}

export const defaultHeaders = {
    "Content-Type": "application/json",
    "Accept": "application/json",
};

const API_VERSION = "v2";
